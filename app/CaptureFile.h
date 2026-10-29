#pragma once
#include <windows.h>
#include <winternl.h>
#include <cstddef>
#include <cstring>
#include <memory>
#include <string>

// Reviewed R7 integration: one owned file, same-parent native leaf rename.
// Preserve pending request storage and the exact object until completion.
struct CaptureFile {
    HANDLE writer=INVALID_HANDLE_VALUE,identity=INVALID_HANDLE_VALUE;
    bool committed=false,deletionMarked=false;
    ULONG renameStatus=0xC000000D,renameCompletionStatus=0xC000000D;
    struct RenameRequest {
        std::unique_ptr<unsigned char[]> storage;
        IO_STATUS_BLOCK completion{};
    };
    std::unique_ptr<RenameRequest> request;
    std::wstring parent;
    using RenameFunction=NTSTATUS(NTAPI*)(HANDLE,PIO_STATUS_BLOCK,PVOID,ULONG,FILE_INFORMATION_CLASS);

    static RenameFunction trustedRename() {
        static const auto function=[]()->RenameFunction {
            // Only an already loaded exact System32 ntdll. Pin its mapping for
            // this process lifetime; no authored DLL path or loader search.
            HMODULE module=GetModuleHandleW(L"ntdll.dll");
            if(!module)return nullptr;
            wchar_t actual[MAX_PATH]{},expected[MAX_PATH]{};
            const DWORD length=GetModuleFileNameW(module,actual,MAX_PATH);
            const UINT system=GetSystemDirectoryW(expected,MAX_PATH);
            constexpr wchar_t suffix[]=L"\\ntdll.dll";
            if(!length||length>=MAX_PATH||!system||system+11>=MAX_PATH)return nullptr;
            std::memcpy(expected+system,suffix,sizeof(suffix));
            if(CompareStringOrdinal(actual,length,expected,system+10,TRUE)!=CSTR_EQUAL)return nullptr;
            HMODULE pinned=nullptr;
            if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
                reinterpret_cast<LPCWSTR>(module),&pinned)||pinned!=module)return nullptr;
            return reinterpret_cast<RenameFunction>(GetProcAddress(module,"NtSetInformationFile"));
        }();
        return function;
    }
    static bool validLeaf(const std::wstring& leaf) {
        if(leaf.empty()||leaf.size()>255||leaf==L"."||leaf==L".."||leaf.back()==L'.')return false;
        for(wchar_t c:leaf)if(!((c>=L'a'&&c<=L'z')||(c>=L'A'&&c<=L'Z')||(c>=L'0'&&c<=L'9')||c==L'_'||c==L'-'||c==L'.'))return false;
        auto base=leaf.substr(0,leaf.find(L'.'));
        for(auto& c:base)if(c>=L'a'&&c<=L'z')c-=L'a'-L'A';
        return !(base==L"CON"||base==L"PRN"||base==L"AUX"||base==L"NUL"
            ||(base.size()==4&&(base.substr(0,3)==L"COM"||base.substr(0,3)==L"LPT")&&base[3]>=L'0'&&base[3]<=L'9'));
    }
    bool create(const std::wstring& staging) {
        const auto slash=staging.find_last_of(L'\\');
        if(slash==std::wstring::npos||slash+1==staging.size()||!validLeaf(staging.substr(slash+1)))return false;
        parent=staging.substr(0,slash); // allocation before acquiring a HANDLE
        writer=CreateFileW(staging.c_str(),GENERIC_WRITE|DELETE,0,nullptr,CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
        if(writer==INVALID_HANDLE_VALUE)return false;
        BY_HANDLE_FILE_INFORMATION info{};
        if(GetFileType(writer)!=FILE_TYPE_DISK||!GetFileInformationByHandle(writer,&info)
            ||info.dwFileAttributes&(FILE_ATTRIBUTE_REPARSE_POINT|FILE_ATTRIBUTE_DIRECTORY))return false;
        HANDLE duplicate=INVALID_HANDLE_VALUE;
        if(!DuplicateHandle(GetCurrentProcess(),writer,GetCurrentProcess(),&duplicate,0,FALSE,DUPLICATE_SAME_ACCESS))return false;
        identity=duplicate;return true;
    }
    bool closeWriter() {
        if(writer==INVALID_HANDLE_VALUE)return true;
        if(!CloseHandle(writer))return false;
        writer=INVALID_HANDLE_VALUE;return true;
    }
    bool rename(const std::wstring& destination) {
        if(writer!=INVALID_HANDLE_VALUE||identity==INVALID_HANDLE_VALUE||request)return false;
        const auto slash=destination.find_last_of(L'\\');
        if(slash==std::wstring::npos||destination.substr(0,slash)!=parent)return false;
        const auto leaf=destination.substr(slash+1);
        if(!validLeaf(leaf))return false;
        const auto function=trustedRename();
        if(!function){renameStatus=renameCompletionStatus=0xC00000BB;return false;}
        static_assert(sizeof(void*)==8&&offsetof(FILE_RENAME_INFO,RootDirectory)==8
            &&offsetof(FILE_RENAME_INFO,FileNameLength)==16&&offsetof(FILE_RENAME_INFO,FileName)==20,
            "Reviewed x64 native rename layout required");
        const auto bytes=sizeof(FILE_RENAME_INFO)+leaf.size()*sizeof(wchar_t)+sizeof(wchar_t);
        request=std::make_unique<RenameRequest>();
        request->storage=std::make_unique<unsigned char[]>(bytes);
        std::memset(request->storage.get(),0,bytes);
        auto* info=reinterpret_cast<FILE_RENAME_INFO*>(request->storage.get());
        info->ReplaceIfExists=FALSE;info->RootDirectory=nullptr;
        info->FileNameLength=static_cast<DWORD>(leaf.size()*sizeof(wchar_t));
        std::memcpy(info->FileName,leaf.data(),info->FileNameLength);
        request->completion.Status=static_cast<NTSTATUS>(0x103);
        const auto status=function(identity,&request->completion,info,static_cast<ULONG>(bytes),static_cast<FILE_INFORMATION_CLASS>(10));
        MemoryBarrier();renameStatus=static_cast<ULONG>(status);
        renameCompletionStatus=static_cast<ULONG>(request->completion.Status);
        if(renameStatus==0x103||(renameStatus==0&&renameCompletionStatus==0x103))return false;
        request.reset();
        committed=renameStatus==0&&renameCompletionStatus==0;
        return committed;
    }
    bool cleanup() {
        if(request){
            // No publication was acknowledged for a pending request. Retain
            // its IO_STATUS_BLOCK/storage and exact object until completion.
            if(WaitForSingleObject(identity,0)!=WAIT_OBJECT_0)return false;
            MemoryBarrier();
            renameCompletionStatus=static_cast<ULONG>(request->completion.Status);
            if(renameCompletionStatus==0x103)return false;
            request.reset();
        }
        if(!committed&&!deletionMarked){
            const auto owned=identity!=INVALID_HANDLE_VALUE?identity:writer;
            if(owned!=INVALID_HANDLE_VALUE){
                FILE_DISPOSITION_INFO disposition{};disposition.DeleteFile=TRUE;
                if(!SetFileInformationByHandle(owned,FileDispositionInfo,&disposition,sizeof(disposition)))return false;
                deletionMarked=true;
            }
        }
        const bool writerClosed=closeWriter();bool identityClosed=true;
        if(identity!=INVALID_HANDLE_VALUE){if(CloseHandle(identity))identity=INVALID_HANDLE_VALUE;else identityClosed=false;}
        return writerClosed&&identityClosed;
    }
    bool released() const {return !request&&writer==INVALID_HANDLE_VALUE&&identity==INVALID_HANDLE_VALUE;}
    ~CaptureFile(){if(request)request.release();} // unknown request is never freed
};
