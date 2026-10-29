using System.Runtime.InteropServices;
using Microsoft.Win32;
namespace VaporLockControllerHost;

internal static class ControllerStateQueries
{
    internal static bool InterpretLocate(uint result) => result switch { 0 => true, 0x0d => false,
        _ => throw new IOException($"Windows controller presence is unknown (CONFIGRET {result}); cleanup remains pending.") };
    internal static bool Live(string id) => InterpretLocate(CM_Locate_DevNodeW(out _, id, 0));
    internal static bool Persistent(string id)
    {
        if (Live(id) || InterpretLocate(CM_Locate_DevNodeW(out _, id, 1))) return true;
        using var key = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Enum\" + id);
        return key is not null;
    }
    internal static bool FilePresent(Func<FileAttributes> query)
    {
        try { query(); return true; }
        catch (FileNotFoundException) { return false; }
    }
    internal static bool FilePresent(string path) => FilePresent(() => File.GetAttributes(path));
    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)] private static extern uint CM_Locate_DevNodeW(out uint node, string id, uint flags);
}
