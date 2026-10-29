using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace VaporLockControllerHost;

// Elevated status output is limited to generated filenames under the invoking
// user's Windows-known local application folder. Retained directory identities
// prevent a writable parent being replaced with a junction during the session.
internal sealed class ControllerSessionStatus : IDisposable
{
    private readonly string path;
    private readonly List<SafeFileHandle> directories = new();
    private FileStream? output;
    internal ControllerSessionStatus(string requested)
    {
        var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VaporLock", "controller-sessions");
        path = Path.GetFullPath(requested);
        if (!Path.GetDirectoryName(path)!.Equals(root, StringComparison.OrdinalIgnoreCase)
            || !Regex.IsMatch(Path.GetFileName(path), @"\A(?:install|uninstall|session)-[0-9a-f]{32}\.json\z", RegexOptions.CultureInvariant))
            throw new IOException("Controller status output is outside its generated session folder.");
        var chain = new Stack<string>();
        for (var item = new DirectoryInfo(root); item is not null; item = item.Parent) chain.Push(item.FullName);
        try
        {
            while (chain.Count != 0)
            {
                var directory = chain.Pop();
                if (!Directory.Exists(directory)) Directory.CreateDirectory(directory);
                // This user-owned namespace needs a no-reparse-write guard;
                // attribute-only handles do not protect reparse data.
                var handle = CreateFileW(directory, 0x20081, 1, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
                if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
                directories.Add(handle);
                if (!GetFileInformationByHandle(handle, out var info) || (info.Attributes & 0x410) != 0x10)
                    throw new IOException("Linked controller status directory rejected.");
                var buffer = new StringBuilder(32768);
                var count = GetFinalPathNameByHandleW(handle, buffer, buffer.Capacity, 0);
                if (count == 0 || count >= buffer.Capacity
                    || !buffer.ToString().Replace(@"\\?\", "").TrimEnd('\\').Equals(directory.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Controller status directory identity changed.");
            }
            var file = CreateFileW(path, 0x40000080, 1, IntPtr.Zero, 1, 0x80200000, IntPtr.Zero);
            if (file.IsInvalid) { file.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
            try
            {
                if (!GetFileInformationByHandle(file, out var info) || (info.Attributes & 0x410) != 0 || info.Links != 1)
                    throw new IOException("Linked or hard-linked controller status file rejected.");
                var finalFile = new StringBuilder(32768);
                var finalCount = GetFinalPathNameByHandleW(file, finalFile, finalFile.Capacity, 0);
                if (finalCount == 0 || finalCount >= finalFile.Capacity
                    || !finalFile.ToString().Equals(@"\\?\" + path, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Controller status file identity changed.");
                output = new FileStream(file, FileAccess.Write, 16384, isAsync: false);
            }
            catch { file.Dispose(); throw; }
        }
        catch { Dispose(); throw; }
    }
    internal void Write(string payload)
    {
        var raw = Encoding.UTF8.GetBytes(payload);
        if (raw.Length > 16384) throw new IOException("Controller status exceeds its output bound.");
        var file = output ?? throw new IOException("The controller status lease is closed.");
        // Update this one retained, newly created identity. GUI readers already
        // retry incomplete JSON; no rename requires dropping parent protection.
        file.Position = 0; file.Write(raw); file.SetLength(raw.Length); file.Flush(true);
    }
    public void Dispose()
    { output?.Dispose(); output = null; for (var index = directories.Count - 1; index >= 0; index--) directories[index].Dispose(); directories.Clear(); }
    [StructLayout(LayoutKind.Sequential)] private struct FileInfo
    { public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Created, Accessed, Written; public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInfo info);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder buffer, int length, uint flags);
}
