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
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract MaxwellSettlement is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ── Data Structures ───────────────────────────────────────────

    struct Provider {
        address addr;
        uint256 stake;              // collateral in wei
        uint256 pricePerPetaflop;   // price in wei per PetaFLOP (1e15 FLOPs)
        uint256 totalFlopsServed;   // cumulative FLOPs delivered
        uint256 totalEarned;        // cumulative earnings in wei
        address teePublicKey;       // TEE enclave public key for attestation
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
    
    IERC20 public mxtToken;

    constructor(address _mxtToken) Ownable(msg.sender) {
        mxtToken = IERC20(_mxtToken);
    }

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
     * @param teePublicKey The Ethereum address representing the TEE public key
     */
    function registerProvider(uint256 pricePerPetaflop, address teePublicKey, uint256 stakeAmount) external {
        require(stakeAmount >= MIN_STAKE, "Insufficient stake");
        require(pricePerPetaflop > 0, "Price must be > 0");
        require(teePublicKey != address(0), "Invalid TEE address");
        require(!providers[msg.sender].isActive, "Already registered");
        
        mxtToken.safeTransferFrom(msg.sender, address(this), stakeAmount);

        providers[msg.sender] = Provider({
            addr: msg.sender,
            stake: stakeAmount,
            pricePerPetaflop: pricePerPetaflop,
            totalFlopsServed: 0,
            totalEarned: 0,
            teePublicKey: teePublicKey,
            isActive: true
        });

        emit ProviderRegistered(msg.sender, stakeAmount, pricePerPetaflop);
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

        mxtToken.safeTransfer(msg.sender, stake);

        emit ProviderDeactivated(msg.sender);
    }

    // ── Consumer Management ───────────────────────────────────────

    /**
     * @notice Deposit funds for compute consumption.
     */
    function deposit(uint256 amount) external {
        require(amount > 0, "Must deposit > 0");
        mxtToken.safeTransferFrom(msg.sender, address(this), amount);
        consumerBalances[msg.sender] += amount;
        emit ConsumerDeposited(msg.sender, amount);
    }

    /**
     * @notice Withdraw unused balance.
     */
    function withdraw(uint256 amount) external nonReentrant {
        require(consumerBalances[msg.sender] >= amount, "Insufficient balance");
        consumerBalances[msg.sender] -= amount;

        mxtToken.safeTransfer(msg.sender, amount);

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
     * @param signature Cryptographic signature from the TEE validating the FLOPs
     */
    function reportExecution(uint256 taskId, uint256 flopsActual, bytes calldata signature) external {
        ComputeTask storage task = tasks[taskId];
        require(msg.sender == task.provider, "Only provider");
        require(task.status == TaskStatus.Pending, "Invalid status");

        Provider storage providerInfo = providers[task.provider];
        
        // ── TEE Attestation Verification ──
        // Message format: taskId + flopsActual
        bytes32 messageHash = keccak256(abi.encodePacked(taskId, flopsActual));
        // Ethereum signed message prefix
        bytes32 ethSignedMessageHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash)
        );
        
        // Recover the signer address from the signature
        address recoveredSigner = recoverSigner(ethSignedMessageHash, signature);
        require(recoveredSigner == providerInfo.teePublicKey, "Invalid TEE signature");

        task.flopsActual = flopsActual;
        task.status = TaskStatus.Executed;

        emit TaskExecuted(taskId, flopsActual);
    }

    /**
     * @notice State Channel Settlement (Incremental or Final)
     * @param taskId The task to report
     * @param flopsActual Actual FLOPs consumed as signed by consumer
     * @param consumerSignature Cryptographic signature from the Consumer authorizing payment
     */
    function settleStateChannel(uint256 taskId, uint256 flopsActual, bytes calldata consumerSignature) external nonReentrant {
        ComputeTask storage task = tasks[taskId];
        require(msg.sender == task.provider, "Only provider");
        require(task.status == TaskStatus.Pending || task.status == TaskStatus.Executed, "Invalid status");
        require(flopsActual > task.flopsActual, "Monotonically increasing FLOPs required");

        // Verify consumer signature
        bytes32 messageHash = keccak256(abi.encodePacked(taskId, flopsActual));
        bytes32 ethSignedMessageHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash)
        );
        address recoveredSigner = recoverSigner(ethSignedMessageHash, consumerSignature);
        require(recoveredSigner == task.consumer, "Invalid consumer signature");

        Provider storage provider = providers[task.provider];

        // Calculate incremental cost
        uint256 incrementalFlops = flopsActual - task.flopsActual;
        uint256 cost = (incrementalFlops * provider.pricePerPetaflop) / 1e15;

        // Cap at max budget
        uint256 remainingBudget = task.maxBudgetWei - task.settledAmount;
        if (cost > remainingBudget) {
            cost = remainingBudget;
        }

        uint256 fee = (cost * PROTOCOL_FEE_BPS) / 10000;
        uint256 providerPayment = cost - fee;

        task.flopsActual = flopsActual;
        task.settledAmount += cost;
        task.settledAt = block.timestamp;
        
        provider.totalFlopsServed += incrementalFlops;
        provider.totalEarned += providerPayment;
        protocolFeePool += fee;

        mxtToken.safeTransfer(task.provider, providerPayment);
        emit TaskSettled(taskId, cost);
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
        mxtToken.safeTransfer(task.provider, providerPayment);

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
        mxtToken.safeTransfer(owner(), amount);
    }

    // ── Internal Helpers ──────────────────────────────────────────

    function recoverSigner(bytes32 _ethSignedMessageHash, bytes memory _signature)
        internal
        pure
        returns (address)
    {
        require(_signature.length == 65, "Invalid signature length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(_signature, 32))
            s := mload(add(_signature, 64))
            v := byte(0, mload(add(_signature, 96)))
        }
        return ecrecover(_ethSignedMessageHash, v, r, s);
    }
}
