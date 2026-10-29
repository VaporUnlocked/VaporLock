#pragma once

// Protocol validation and process ownership remain enforced independently from
// licensing. The historical pipe namespace is retained for worker compatibility.
namespace cloud_native {
enum class Operation { Inspect, Infer };
inline bool validOperation(Operation operation) {
    return operation == Operation::Inspect || operation == Operation::Infer;
}
inline const wchar_t* pipePrefix() {
    return L"VaporLock.Cloud.premium.";
}
}
