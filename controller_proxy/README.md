# Vapor Lock XInput compatibility proxy

This optional x64 DLL is an application-scoped compatibility layer. When a
compatible camera application loads it as `xinput1_4.dll`, it forwards all
normal XInput calls to Windows and adds only the current stick request from
the local `VaporLockXInputProxy` shared-memory mapping.

It does not install a driver, hide a physical device, inject into processes, or
modify the physical controller. The target application must explicitly load
the DLL from its own directory or through its documented plugin/launch path.

The native Vapor Lock publisher and GUI integration will be added separately.
Until then this DLL is a protocol component only and is not packaged into the
portable releases.
