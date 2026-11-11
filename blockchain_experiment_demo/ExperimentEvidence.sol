// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

/// @notice 学习用试验存证合约。完整数据链外保存，链上保存 SHA-256 指纹。
contract ExperimentEvidence {
    struct Record {
        bytes32 digest;       // Python 对整份试验 JSON 计算的 SHA-256
        uint256 submittedAt;  // 打包区块的时间，单位为秒
    }

    // 每个提交账户拥有自己的试验编号空间。
    // 同一账户的同一编号只允许登记一次。
    mapping(address => mapping(bytes32 => Record)) public records;

    event Registered(
        address indexed submitter,
        bytes32 indexed experimentId,
        bytes32 digest
    );

    function register(bytes32 experimentId, bytes32 digest) external {
        require(experimentId != bytes32(0), "Empty experiment ID");
        require(digest != bytes32(0), "Empty digest");
        require(records[msg.sender][experimentId].digest == bytes32(0), "Already registered");

        records[msg.sender][experimentId] = Record(digest, block.timestamp);
        emit Registered(msg.sender, experimentId, digest);
    }

    /// @notice 调用者传入本地重新计算的指纹，检查是否与指定提交者的存证一致。
    function verify(
        address submitter,
        bytes32 experimentId,
        bytes32 candidateDigest
    ) external view returns (bool) {
        bytes32 saved = records[submitter][experimentId].digest;
        return saved != bytes32(0) && saved == candidateDigest;
    }
}
