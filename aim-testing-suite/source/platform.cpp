#include <windows.h>
#include <shellapi.h>
#include <objbase.h>
#include <filesystem>
#include <string>
#include <stdexcept>
#include <algorithm>
#include <span>
namespace suite {
namespace fs=std::filesystem;
std::span<const unsigned char> bundledResource(int id){
    auto module=GetModuleHandleW(nullptr);
    auto resource=FindResourceW(module,MAKEINTRESOURCEW(id),MAKEINTRESOURCEW(10));
    if(!resource)throw std::runtime_error("A required asset is missing from this executable. Copy the complete EXE again.");
    auto size=SizeofResource(module,resource);auto loaded=LoadResource(module,resource);
    auto bytes=static_cast<const unsigned char*>(LockResource(loaded));
    if(!bytes||!size)throw std::runtime_error("An embedded asset could not be read.");
    return {bytes,size};
}
fs::path executableFolder() {
    wchar_t name[32768];
    DWORD n=GetModuleFileNameW(nullptr,name,32768);
    if(n==0 || n>=32768) throw std::runtime_error("Cannot locate the program.");
    return fs::path(std::wstring(name,n)).parent_path();
}
void showError(const std::string& text) {MessageBoxA(nullptr,text.c_str(),"Vapor Lock | Aim Testing Suite",MB_OK|MB_ICONERROR);}
unsigned long ownProcessId(){return GetCurrentProcessId();}
void replaceFile(const fs::path& temporary,const fs::path& destination){
    if(!MoveFileExW(temporary.c_str(),destination.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("Could not replace the local settings file.");
}
static HWND checkedOwnWindow(void* handle) {
    auto window=static_cast<HWND>(handle);DWORD process=0;
    GetWindowThreadProcessId(window,&process);
    if(!IsWindow(window)||process!=GetCurrentProcessId())throw std::runtime_error("Invalid application window.");
    return window;
}
bool openBrandWebsite(void* handle) {
    auto window=checkedOwnWindow(handle);
    const HRESULT initialized=CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED|COINIT_DISABLE_OLE1DDE);
    // Fixed HTTPS destination. Only called by an explicit menu activation.
    auto result=ShellExecuteW(window,L"open",L"https://github.com/VaporUnlocked/VaporLock",nullptr,nullptr,SW_SHOWNORMAL);
    if(SUCCEEDED(initialized))CoUninitialize();
    return reinterpret_cast<INT_PTR>(result)>32;
}
void showStartupWindow(void* handle,bool activate) {
    auto window=checkedOwnWindow(handle);
    MONITORINFO monitor{};monitor.cbSize=sizeof(monitor);
    if(!GetMonitorInfoW(MonitorFromWindow(window,MONITOR_DEFAULTTONEAREST),&monitor))
        throw std::runtime_error("Cannot locate the application's display.");
    RECT rect{};GetWindowRect(window,&rect);
    const LONG width=std::min(rect.right-rect.left,std::max(320L,monitor.rcWork.right-monitor.rcWork.left-24));
    const LONG height=std::min(rect.bottom-rect.top,std::max(240L,monitor.rcWork.bottom-monitor.rcWork.top-24));
    const LONG x=monitor.rcWork.left+(monitor.rcWork.right-monitor.rcWork.left-width)/2;
    const LONG y=monitor.rcWork.top+(monitor.rcWork.bottom-monitor.rcWork.top-height)/2;
    // Only the window created by this process is restored or repositioned.
    ShowWindow(window,activate?SW_SHOWNORMAL:SW_SHOWNOACTIVATE);
    if(!SetWindowPos(window,activate?HWND_TOP:nullptr,x,y,width,height,
        SWP_SHOWWINDOW|(activate?0:SWP_NOACTIVATE|SWP_NOZORDER)))
        throw std::runtime_error("Cannot show the application window.");
    if(activate)SetForegroundWindow(window); // Respect Windows' foreground permission rules.
}
bool ownWindowIsVisibleOnScreen(void* handle) {
    auto window=checkedOwnWindow(handle);RECT rect{},intersection{};
    MONITORINFO monitor{};monitor.cbSize=sizeof(monitor);
    return IsWindowVisible(window)&&!IsIconic(window)&&GetWindowRect(window,&rect)&&
        GetMonitorInfoW(MonitorFromWindow(window,MONITOR_DEFAULTTONEAREST),&monitor)&&
        IntersectRect(&intersection,&rect,&monitor.rcWork)&&
        intersection.right-intersection.left>=std::min(320L,rect.right-rect.left)&&
        intersection.bottom-intersection.top>=std::min(240L,rect.bottom-rect.top);
}
bool isReparsePoint(const fs::path& path) {
    DWORD attributes=GetFileAttributesW(path.c_str());
    return attributes!=INVALID_FILE_ATTRIBUTES&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT)!=0;
}
}
