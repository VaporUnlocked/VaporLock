using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace VaporLockControllerHost;

internal sealed class ControllerChildJob : IDisposable
{
    private readonly Process child;
    private readonly SafeFileHandle job;
    internal ControllerChildJob(Process process)
    {
        child = process;
        job = CreateJobObjectW(IntPtr.Zero, null);
        if (job.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        var limits = new ExtendedLimits { Basic = new BasicLimits { Flags = 0x2208, ActiveProcesses = 2 }, JobMemory = 1024u * 1024u * 1024u };
        try
        {
            if (!SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf<ExtendedLimits>())
                || !AssignProcessToJobObject(job, child.Handle))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        catch
        {
            try { if (!child.HasExited) child.Kill(entireProcessTree: true); child.WaitForExit(10000); }
            finally { job.Dispose(); }
            throw;
        }
    }
    public void Dispose()
    {
        // Closing the kill-on-close Job owns any surviving helper descendant.
        job.Dispose();
        if (!child.HasExited && !child.WaitForExit(10000))
            throw new IOException("A controller helper did not finish cleanup.");
    }
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits
    { public long ProcessTime, JobTime; public uint Flags; public nuint MinimumWorkingSet, MaximumWorkingSet; public uint ActiveProcesses; public nuint Affinity; public uint Priority, Scheduling; }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters
    { public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits
    { public BasicLimits Basic; public IoCounters Io; public nuint ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateJobObjectW(IntPtr security, string? name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(SafeFileHandle job, int information, ref ExtendedLimits limits, int length);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);
}
