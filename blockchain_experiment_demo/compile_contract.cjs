// 使用固定版本的本地 solc 编译合约，输出 ABI 和 EVM 字节码给 Python。
const fs = require("node:fs");
const path = require("node:path");
const solc = require("solc");

const input = {
  language: "Solidity",
  sources: {
    "ExperimentEvidence.sol": {
      content: fs.readFileSync(path.join(__dirname, "ExperimentEvidence.sol"), "utf8"),
    },
  },
  settings: {
    evmVersion: "paris",
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
  },
};
const output = JSON.parse(solc.compile(JSON.stringify(input)));
for (const diagnostic of output.errors || []) {
  console.error(diagnostic.formattedMessage);
}
if ((output.errors || []).some((item) => item.severity === "error")) {
  process.exit(1);
}
const contract = output.contracts["ExperimentEvidence.sol"].ExperimentEvidence;
process.stdout.write(JSON.stringify({
  abi: contract.abi,
  bytecode: contract.evm.bytecode.object,
}));
