using System.Buffers.Binary;
using System.Runtime.InteropServices;

namespace VaporLockControllerHost;

internal readonly record struct ControllerFrame(
    ushort Buttons, byte LeftTrigger, byte RightTrigger,
    short LeftX, short LeftY, short RightX, short RightY,
    short TrackingX, short TrackingY, byte Flags);

internal static class ControllerFrameReader
{
    internal const uint Magic = 0x48434C56;
    internal const ushort Version = 2;
    internal const int FrameSize = 32;
    internal const int LeaseMilliseconds = 250;
    // Pin the wire clock to the native sender's clock, independent of changes
    // to Environment.TickCount64 semantics in future .NET versions.
    [DllImport("kernel32.dll", EntryPoint = "GetTickCount64")]
    internal static extern ulong CurrentTick();

    internal static async Task ConsumeAsync(Stream stream, Action<ControllerFrame> submit,
        Action neutralize, CancellationToken token)
    {
        var buffer = new byte[FrameSize];
        try
        {
            while (true)
            {
                token.ThrowIfCancellationRequested();
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
                deadline.CancelAfter(LeaseMilliseconds);
                try
                {
                    if (!await ReadPartAsync(stream, buffer.AsMemory(0, 6), deadline.Token))
                        return;
                    if (BinaryPrimitives.ReadUInt32LittleEndian(buffer.AsSpan(0, 4)) != Magic
                        || BinaryPrimitives.ReadUInt16LittleEndian(buffer.AsSpan(4, 2)) != Version)
                        throw new InvalidDataException("Controller bridge protocol mismatch. Use the host bundled with this engine.");
                    if (!await ReadPartAsync(stream, buffer.AsMemory(6), deadline.Token))
                        throw new EndOfStreamException("The controller bridge ended inside a frame.");
                }
                catch (OperationCanceledException) when (!token.IsCancellationRequested)
                {
                    throw new TimeoutException("The controller bridge stopped receiving fresh input.");
                }

                token.ThrowIfCancellationRequested();
                var sentAt = BinaryPrimitives.ReadUInt64LittleEndian(buffer.AsSpan(24, 8));
                var now = CurrentTick();
                if (sentAt > now || now - sentAt > LeaseMilliseconds)
                    throw new InvalidDataException("The controller bridge rejected an expired input frame.");
                if (buffer[23] != 0 || (buffer[22] & ~0x1f) != 0)
                    throw new InvalidDataException("The controller bridge received invalid frame flags.");

                submit(new ControllerFrame(
                    BinaryPrimitives.ReadUInt16LittleEndian(buffer.AsSpan(6, 2)), buffer[8], buffer[9],
                    BinaryPrimitives.ReadInt16LittleEndian(buffer.AsSpan(10, 2)),
                    BinaryPrimitives.ReadInt16LittleEndian(buffer.AsSpan(12, 2)),
                    BinaryPrimitives.ReadInt16LittleEndian(buffer.AsSpan(14, 2)),
                    BinaryPrimitives.ReadInt16LittleEndian(buffer.AsSpan(16, 2)),
                    BinaryPrimitives.ReadInt16LittleEndian(buffer.AsSpan(18, 2)),
                    BinaryPrimitives.ReadInt16LittleEndian(buffer.AsSpan(20, 2)), buffer[22]));
            }
        }
        finally
        {
            // Silence, malformed/partial frames, stop, EOF and failed reads all
            // release buttons/triggers/sticks before the device is disposed.
            neutralize();
        }
    }

    private static async Task<bool> ReadPartAsync(Stream stream, Memory<byte> buffer, CancellationToken token)
    {
        var offset = 0;
        while (offset < buffer.Length)
        {
            var count = await stream.ReadAsync(buffer[offset..], token);
            if (count == 0)
            {
                if (offset != 0)
                    throw new EndOfStreamException("The controller bridge ended inside a frame.");
                return false;
            }
            offset += count;
        }
        return true;
    }
}
