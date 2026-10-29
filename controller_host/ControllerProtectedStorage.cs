using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;

namespace VaporLockControllerHost;

// A component-owned, administrator-only namespace. Creation supplies its DACL
// atomically; pre-existing weak/foreign roots are rejected, never repaired by ACL.
internal sealed class ControllerProtectedStorage : IDisposable
{
    internal const string Security = "O:BAG:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";
    internal string Root { get; }
    private readonly List<SafeFileHandle> directories = new();
    private readonly HashSet<string> heldDirectories = new(StringComparer.OrdinalIgnoreCase);

    internal ControllerProtectedStorage()
    {
        var id = new Guid("62AB5D82-FDC1-4DC3-A9DD-070D1D495D97"); // FOLDERID_ProgramData
        var result = SHGetKnownFolderPath(ref id, 0, IntPtr.Zero, out var pointer);
        if (result != 0) Marshal.ThrowExceptionForHR(result);
        string programData;
        try { programData = Marshal.PtrToStringUni(pointer) ?? throw new IOException("Windows did not provide ProgramData."); }
        finally { Marshal.FreeCoTaskMem(pointer); }
        Root = Path.Combine(programData, "VaporLockControllerComponent");
        try
        {
            HoldAncestors(programData);
            CreateProtectedDirectory(Root);
        }
        catch { Dispose(); throw; }
    }

    private void HoldAncestors(string selected)
    {
        var chain = new Stack<string>();
        for (var path = new DirectoryInfo(selected); path is not null; path = path.Parent) chain.Push(path.FullName);
        while (chain.Count != 0) HoldDirectory(chain.Pop(), false);
    }

    private SafeFileHandle HoldDirectory(string path, bool owned)
    {
        if (heldDirectories.Contains(path)) return directories.First(handle => FinalPath(handle).Equals(Path.GetFullPath(path).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase));
        // Administrator-owned directories have a verified protected BA/SYSTEM
        // DACL, so unprivileged reparse writes are denied by that ACL. Share
        // writes here to allow Windows atomic journal replacement; still deny
        // deletion/renaming of the directory identity. Unowned ancestors retain
        // the stronger LIST_DIRECTORY/no-write-share guard.
        var handle = CreateFileW(path, owned ? 0x20080u : 0x20081u, owned ? 3u : 1u, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try
        {
            ValidateHandle(handle, path, directory: true);
            if (owned) VerifyOwnedSecurity(handle);
            directories.Add(handle); heldDirectories.Add(path);
            return handle;
        }
        catch { handle.Dispose(); throw; }
    }

    private void CreateProtectedDirectory(string path)
    {
        var descriptor = new RawSecurityDescriptor(Security);
        var raw = new byte[descriptor.BinaryLength]; descriptor.GetBinaryForm(raw, 0);
        var pinned = GCHandle.Alloc(raw, GCHandleType.Pinned);
        var attributes = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = pinned.AddrOfPinnedObject() };
        try
        {
            if (!CreateDirectoryW(path, ref attributes))
            {
                var error = Marshal.GetLastWin32Error();
                if (error != 183) throw new Win32Exception(error);
                // ERROR_ALREADY_EXISTS is not permission to change its owner/DACL.
            }
        }
        finally { pinned.Free(); }
        HoldDirectory(path, true);
    }

    internal string CreateChild(string name)
    {
        ValidateName(name);
        var path = Path.Combine(Root, name);
        CreateProtectedDirectory(path);
        return path;
    }

    internal FileStream HoldInput(string path)
    {
        path = Path.GetFullPath(path);
        if (!path.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Controller input is outside protected component storage.");
        var relative = Path.GetRelativePath(Root, Path.GetDirectoryName(path)!);
        var current = Root;
        if (relative != ".") foreach (var name in relative.Split(Path.DirectorySeparatorChar))
        {
            ValidateName(name); current = Path.Combine(current, name); HoldDirectory(current, true);
        }
        var handle = CreateFileW(path, 0x80020000, 1, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
        if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try
        {
            ValidateHandle(handle, path, directory: false); VerifyOwnedSecurity(handle);
            return new FileStream(handle, FileAccess.Read, 65536, isAsync: false);
        }
        catch { handle.Dispose(); throw; }
    }

    internal FileStream CreateOutput(string path)
    {
        path = Path.GetFullPath(path);
        if (!path.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Controller output is outside protected component storage.");
        var parent = Path.GetDirectoryName(path)!;
        HoldDirectory(parent, true);
        ValidateName(Path.GetFileName(path));
        var descriptor = new RawSecurityDescriptor(Security);
        var raw = new byte[descriptor.BinaryLength]; descriptor.GetBinaryForm(raw, 0);
        var pinned = GCHandle.Alloc(raw, GCHandleType.Pinned);
        var attributes = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = pinned.AddrOfPinnedObject() };
        SafeFileHandle handle;
        try
        {
            // Explicit administrator ownership at creation, not merely an
            // inherited DACL with the unelevated user's SID as default owner.
            handle = CreateFileWithSecurity(path, 0x40020000, 0, ref attributes, 1, 0x80000080, IntPtr.Zero);
        }
        finally { pinned.Free(); }
        if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try
        {
            ValidateHandle(handle, path, false); VerifyOwnedSecurity(handle);
            return new FileStream(handle, FileAccess.Write, 65536, isAsync: false);
        }
        catch { handle.Dispose(); throw; }
    }

    internal void WriteJournal<T>(string name, T value)
    {
        ValidateName(name);
        if (!name.EndsWith(".json", StringComparison.Ordinal)) throw new IOException("Invalid component journal name.");
        var path = Path.Combine(Root, name);
        if (ControllerStateQueries.FilePresent(path)) using (HoldInput(path)) { }
        var raw = JsonSerializer.SerializeToUtf8Bytes(value);
        if (raw.Length > 256 * 1024) throw new IOException("Controller journal exceeds its supported limit.");
        var temporary = path + ".new-" + Guid.NewGuid().ToString("N");
        using (var stream = CreateOutput(temporary))
        { stream.Write(raw); stream.Flush(flushToDisk: true); }
        File.Move(temporary, path, overwrite: true);
        using var readback = HoldInput(path);
        if (readback.Length != raw.Length || !ReadBytes(readback, 256 * 1024).AsSpan().SequenceEqual(raw))
            throw new IOException("Controller journal readback failed.");
    }

    internal T? ReadJournal<T>(string name)
    {
        ValidateName(name);
        var path = Path.Combine(Root, name);
        if (!ControllerStateQueries.FilePresent(path)) return default;
        using var input = HoldInput(path);
        return JsonSerializer.Deserialize<T>(ReadBytes(input, 256 * 1024))
            ?? throw new IOException("Invalid controller installation record.");
    }

    internal static byte[] ReadBytes(FileStream input, int maximum)
    {
        if (input.Length > maximum) throw new IOException("Controller metadata/resource exceeds its supported limit.");
        input.Position = 0;
        var bytes = new byte[checked((int)input.Length)]; input.ReadExactly(bytes); return bytes;
    }

    private static void ValidateName(string name)
    {
        if (string.IsNullOrWhiteSpace(name) || name.Length > 150 || name.Any(c => !(char.IsAsciiLetterOrDigit(c) || c is '.' or '-' or '_'))
            || name is "." or ".." || name.EndsWith('.') || name.IndexOf(':') >= 0)
            throw new IOException("Invalid protected component path segment.");
    }

    private static void ValidateHandle(SafeFileHandle handle, string expected, bool directory)
    {
        if (!GetFileInformationByHandle(handle, out var info)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if ((info.Attributes & 0x400) != 0 || ((info.Attributes & 0x10) != 0) != directory || !directory && info.Links != 1
            || !FinalPath(handle).Equals(Path.GetFullPath(expected).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            throw new IOException("Linked, hard-linked or replaced controller path rejected.");
    }

    private static string FinalPath(SafeFileHandle handle)
    {
        var buffer = new StringBuilder(32768);
        var count = GetFinalPathNameByHandleW(handle, buffer, buffer.Capacity, 0);
        if (count == 0 || count >= buffer.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error());
        var value = buffer.ToString();
        return (value.StartsWith("\\\\?\\", StringComparison.Ordinal) ? value[4..] : value).TrimEnd('\\');
    }

    private static void VerifyOwnedSecurity(SafeFileHandle handle)
    {
        var error = GetSecurityInfo(handle, 1, 5, out _, out _, out _, out _, out var pointer);
        if (error != 0) throw new Win32Exception((int)error);
        try
        {
            var size = GetSecurityDescriptorLength(pointer);
            if (size == 0 || size > 65536) throw new IOException("Invalid controller storage security descriptor.");
            var bytes = new byte[size]; Marshal.Copy(pointer, bytes, 0, bytes.Length);
            ValidateStorageSecurity(new RawSecurityDescriptor(bytes, 0));
        }
        finally { LocalFree(pointer); }
    }

    internal static void ValidateStorageSecurity(RawSecurityDescriptor descriptor)
    {
        var admin = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
        var acl = descriptor.DiscretionaryAcl;
        if ((descriptor.Owner != admin && descriptor.Owner != system) || acl is null || acl.Count != 2
            || (descriptor.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0)
            throw new IOException("Existing controller storage is not exclusively administrator owned.");
        var seen = new HashSet<string>();
        foreach (GenericAce raw in acl)
        {
            if (raw is not CommonAce ace || ace.IsCallback || ace.AceQualifier != AceQualifier.AccessAllowed
                || ace.AccessMask != 0x1f01ff || (ace.SecurityIdentifier != admin && ace.SecurityIdentifier != system)
                || (ace.AceFlags & (AceFlags.InheritOnly | AceFlags.NoPropagateInherit)) != 0
                || !seen.Add(ace.SecurityIdentifier.Value))
                throw new IOException("Existing controller storage has an unexpected access rule.");
        }
    }

    public void Dispose() { for (var index = directories.Count - 1; index >= 0; index--) directories[index].Dispose(); directories.Clear(); }

    [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes { public int Length; public IntPtr Descriptor; public int Inherit; }
    [StructLayout(LayoutKind.Sequential)] private struct FileInfo
    { public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Created, Accessed, Written; public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] private static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateFileWithSecurity(string path, uint access, uint share, ref SecurityAttributes security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CreateDirectoryW(string path, ref SecurityAttributes security);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInfo info);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder buffer, int length, uint flags);
    [DllImport("advapi32.dll")] private static extern uint GetSecurityInfo(SafeFileHandle handle, int type, uint parts, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll")] private static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr memory);
}
