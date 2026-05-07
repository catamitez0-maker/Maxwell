// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title MaxwellSettlement
 * @notice Trustless FLOPs-based compute settlement contract.
 *
 * This contract implements the Maxwell Protocol's on-chain settlement layer:
 * - Compute providers register with staked collateral
 * - Consumers deposit funds and submit compute tasks
 * - Settlement is based on verified FLOPs consumption (reported by oracle)
 * - Disputes can be raised within a challenge window
 *
 * Pricing formula: cost = flops_consumed × price_per_petaflop / 1e15
 */

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract MaxwellSettlement is Ownable, ReentrancyGuard {

    // ── Data Structures ───────────────────────────────────────────

    struct Provider {
        address addr;
        uint256 stake;              // collateral in wei
        uint256 pricePerPetaflop;   // price in wei per PetaFLOP (1e15 FLOPs)
        uint256 totalFlopsServed;   // cumulative FLOPs delivered
        uint256 totalEarned;        // cumulative earnings in wei
        bool isActive;
    }

    struct ComputeTask {
        uint256 taskId;
        address consumer;
        address provider;
        uint256 flopsEstimated;     // pre-estimated FLOPs
        uint256 flopsActual;        // oracle-reported actual FLOPs
        uint256 maxBudgetWei;       // consumer's max willingness to pay
        uint256 settledAmount;      // final settled amount
        TaskStatus status;
        uint256 submittedAt;
        uint256 settledAt;
    }

    enum TaskStatus {
        Pending,        // submitted, awaiting execution
        Executed,       // provider reports completion
        Settled,        // payment finalized
        Disputed,       // under dispute
        Refunded        // refunded to consumer
    }

    // ── State ─────────────────────────────────────────────────────

    uint256 public constant CHALLENGE_WINDOW = 1 hours;
    uint256 public constant MIN_STAKE = 0.01 ether;
    uint256 public constant PROTOCOL_FEE_BPS = 50; // 0.5% protocol fee

    mapping(address => Provider) public providers;
    mapping(uint256 => ComputeTask) public tasks;
    mapping(address => uint256) public consumerBalances;

    uint256 public taskCounter;
    uint256 public protocolFeePool;

    // ── Events ────────────────────────────────────────────────────

    event ProviderRegistered(address indexed provider, uint256 stake, uint256 pricePerPetaflop);
    event ProviderDeactivated(address indexed provider);
    event ConsumerDeposited(address indexed consumer, uint256 amount);
    event ConsumerWithdrawn(address indexed consumer, uint256 amount);
    event TaskSubmitted(uint256 indexed taskId, address indexed consumer, address indexed provider, uint256 flopsEstimated);
    event TaskExecuted(uint256 indexed taskId, uint256 flopsActual);
    event TaskSettled(uint256 indexed taskId, uint256 amount);
    event TaskDisputed(uint256 indexed taskId, address indexed disputedBy);
    event TaskRefunded(uint256 indexed taskId, uint256 amount);

    // ── Provider Management ───────────────────────────────────────

    /**
     * @notice Register as a compute provider with staked collateral.
     * @param pricePerPetaflop Price in wei per PetaFLOP of compute
     */
    function registerProvider(uint256 pricePerPetaflop) external payable {
        require(msg.value >= MIN_STAKE, "Insufficient stake");
        require(pricePerPetaflop > 0, "Price must be > 0");
        require(!providers[msg.sender].isActive, "Already registered");

        providers[msg.sender] = Provider({
            addr: msg.sender,
            stake: msg.value,
            pricePerPetaflop: pricePerPetaflop,
            totalFlopsServed: 0,
            totalEarned: 0,
            isActive: true
        });

        emit ProviderRegistered(msg.sender, msg.value, pricePerPetaflop);
    }

    /**
     * @notice Deactivate provider and return stake (after all tasks settled).
     */
    function deactivateProvider() external nonReentrant {
        Provider storage p = providers[msg.sender];
        require(p.isActive, "Not active");

        p.isActive = false;
        uint256 stake = p.stake;
        p.stake = 0;

        (bool sent, ) = msg.sender.call{value: stake}("");
        require(sent, "Stake return failed");

        emit ProviderDeactivated(msg.sender);
    }

    // ── Consumer Management ───────────────────────────────────────

    /**
     * @notice Deposit funds for compute consumption.
     */
    function deposit() external payable {
        require(msg.value > 0, "Must deposit > 0");
        consumerBalances[msg.sender] += msg.value;
        emit ConsumerDeposited(msg.sender, msg.value);
    }

    /**
     * @notice Withdraw unused balance.
     */
    function withdraw(uint256 amount) external nonReentrant {
        require(consumerBalances[msg.sender] >= amount, "Insufficient balance");
        consumerBalances[msg.sender] -= amount;

        (bool sent, ) = msg.sender.call{value: amount}("");
        require(sent, "Withdraw failed");

        emit ConsumerWithdrawn(msg.sender, amount);
    }

    // ── Task Lifecycle ────────────────────────────────────────────

    /**
     * @notice Submit a compute task with estimated FLOPs and budget.
     * @param providerAddr Address of the chosen compute provider
     * @param flopsEstimated Estimated FLOPs for this task
     * @param maxBudgetWei Maximum the consumer is willing to pay
     */
    function submitTask(
        address providerAddr,
        uint256 flopsEstimated,
        uint256 maxBudgetWei
    ) external returns (uint256) {
        require(providers[providerAddr].isActive, "Provider not active");
        require(consumerBalances[msg.sender] >= maxBudgetWei, "Insufficient balance");
        require(flopsEstimated > 0, "FLOPs must be > 0");

        // Lock budget from consumer balance
        consumerBalances[msg.sender] -= maxBudgetWei;

        uint256 taskId = taskCounter++;
        tasks[taskId] = ComputeTask({
            taskId: taskId,
            consumer: msg.sender,
            provider: providerAddr,
            flopsEstimated: flopsEstimated,
            flopsActual: 0,
            maxBudgetWei: maxBudgetWei,
            settledAmount: 0,
            status: TaskStatus.Pending,
            submittedAt: block.timestamp,
            settledAt: 0
        });

        emit TaskSubmitted(taskId, msg.sender, providerAddr, flopsEstimated);
        return taskId;
    }

    /**
     * @notice Provider reports task completion with actual FLOPs.
     * @param taskId The task to report
     * @param flopsActual Actual FLOPs consumed (from oracle measurement)
     */
    function reportExecution(uint256 taskId, uint256 flopsActual) external {
        ComputeTask storage task = tasks[taskId];
        require(msg.sender == task.provider, "Only provider");
        require(task.status == TaskStatus.Pending, "Invalid status");

        task.flopsActual = flopsActual;
        task.status = TaskStatus.Executed;

        emit TaskExecuted(taskId, flopsActual);
    }

    /**
     * @notice Settle a task after challenge window expires.
     *         Calculates payment based on actual FLOPs × provider's rate.
     */
    function settleTask(uint256 taskId) external nonReentrant {
        ComputeTask storage task = tasks[taskId];
        require(task.status == TaskStatus.Executed, "Not executed");
        require(
            block.timestamp >= task.submittedAt + CHALLENGE_WINDOW,
            "Challenge window active"
        );

        Provider storage provider = providers[task.provider];

        // cost = flopsActual × pricePerPetaflop / 1e15
        uint256 cost = (task.flopsActual * provider.pricePerPetaflop) / 1e15;

        // Cap at max budget
        if (cost > task.maxBudgetWei) {
            cost = task.maxBudgetWei;
        }

        // Protocol fee
        uint256 fee = (cost * PROTOCOL_FEE_BPS) / 10000;
        uint256 providerPayment = cost - fee;

        // Refund excess budget to consumer
        uint256 refund = task.maxBudgetWei - cost;
        if (refund > 0) {
            consumerBalances[task.consumer] += refund;
        }

        // Pay provider
        provider.totalFlopsServed += task.flopsActual;
        provider.totalEarned += providerPayment;
        protocolFeePool += fee;

        task.settledAmount = cost;
        task.settledAt = block.timestamp;
        task.status = TaskStatus.Settled;

        // Transfer to provider
        (bool sent, ) = task.provider.call{value: providerPayment}("");
        require(sent, "Payment failed");

        emit TaskSettled(taskId, cost);
    }

    /**
     * @notice Dispute a task within the challenge window.
     */
    function disputeTask(uint256 taskId) external {
        ComputeTask storage task = tasks[taskId];
        require(
            msg.sender == task.consumer || msg.sender == task.provider,
            "Not a party"
        );
        require(
            task.status == TaskStatus.Executed,
            "Can only dispute executed tasks"
        );
        require(
            block.timestamp < task.submittedAt + CHALLENGE_WINDOW,
            "Challenge window expired"
        );

        task.status = TaskStatus.Disputed;
        emit TaskDisputed(taskId, msg.sender);
    }

    /**
     * @notice Owner resolves a dispute (simplified — production would use a DAO/oracle).
     */
    function resolveDispute(uint256 taskId, bool refundConsumer) external onlyOwner {
        ComputeTask storage task = tasks[taskId];
        require(task.status == TaskStatus.Disputed, "Not disputed");

        if (refundConsumer) {
            consumerBalances[task.consumer] += task.maxBudgetWei;
            task.status = TaskStatus.Refunded;
            emit TaskRefunded(taskId, task.maxBudgetWei);
        } else {
            task.status = TaskStatus.Executed;
            // Can be settled normally now
        }
    }

    // ── View Functions ────────────────────────────────────────────

    /**
     * @notice Estimate cost for a task before submission.
     */
    function estimateCost(
        address providerAddr,
        uint256 flopsEstimated
    ) external view returns (uint256 cost, uint256 fee) {
        Provider storage p = providers[providerAddr];
        cost = (flopsEstimated * p.pricePerPetaflop) / 1e15;
        fee = (cost * PROTOCOL_FEE_BPS) / 10000;
    }

    /**
     * @notice Withdraw accumulated protocol fees.
     */
    function withdrawProtocolFees() external onlyOwner nonReentrant {
        uint256 amount = protocolFeePool;
        protocolFeePool = 0;
        (bool sent, ) = owner().call{value: amount}("");
        require(sent, "Fee withdrawal failed");
    }

    receive() external payable {
        consumerBalances[msg.sender] += msg.value;
    }
}
