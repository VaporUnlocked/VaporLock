# Vapor Lock — Aim Testing Suite

A local C++20 practice range with four connected bays, moving character targets, physical settings panels and a single Esc menu. The cleared CC0 SWAT character is included. Vapor Lock branding and the blue-black/ice-blue palette take direction from **VaporLock.top**.

## Start

Open **Aim Testing Suite.exe**. The portable release needs only this one file: the C++ runtime is statically linked, and the included SWAT character, interface font, icon, credits and instructions are inside the executable. There is no installer, Python/.NET requirement, separate runtime installer or asset download. Existing local shortcuts continue to work.

On first launch it creates **models** beside the EXE for custom `.glb` characters, along with local reports and text guides. Built-in models and the font load directly from executable resources, without extraction. Settings and output stay beside the EXE even when launched from a different working directory. Use a writable location; no administrator access is required. Copy just the EXE for a fresh setup, or move the whole portable folder to retain preferences and custom models. Windows x64 and a working OpenGL 3.3-capable graphics driver are required.

Click once inside the window to enter; that first click does not fire. The window prepares its first frame before appearing and centers itself within the display's work area.

| Control | Action |
| --- | --- |
| Mouse | Look |
| Left mouse | Fire one shot; activate the physical tile under the crosshair |
| W / A / S / D | Walk through the range |
| Shift | Sprint |
| Ctrl | Crouch and walk slowly |
| Esc | Pause / return; release / capture this window's mouse |
| F11 | Toggle borderless window |
| F12 | Save a clean range image with target metadata |

**Esc contains character selection, a Quality dropdown, Unstuck Player, Exit Range and the GitHub / Project source link.** Tab/Shift+Tab and Enter/Space select these actions. In the open quality list, use the mouse or Up/Down and Enter. Esc or an outside click closes the list first. Local character choices refresh when the menu opens. Unstuck returns you to a clear position in your current bay. Switching away from the application releases its cursor and pauses practice.

Quality applies immediately and persists locally. **Mid is the default**, including when loading older preferences that have no quality field. Selecting a preset restores that preset's anti-aliasing setting and saves/ends any timed drill so its measurements do not mix visual conditions.

| Quality | Anti-aliasing | Dynamic shadows | Decorative effects |
| --- | --- | --- | --- |
| Max | Up to 8x MSAA | 4096 px, nine filter taps | Full surfaces and neon glow |
| High | Up to 4x MSAA | 2048 px, nine filter taps | Full surfaces and neon glow |
| Mid (default) | Up to 2x MSAA | 1024 px, one sample | Detailed surfaces and neon glow |
| Potato | Off | Off | Simple surfaces and bright accent strips; no world halos |

All presets keep the same character geometry, hit tests, target counts and native render resolution. Text and UI controls remain readable, with no text glow. Actual MSAA is limited by the GPU; menu and panel text remain full resolution.

The project source link opens **https://github.com/VaporUnlocked/VaporLock** in your default browser only when selected. It leaves the range paused and sends no model, score or configuration data. The range itself has no web client, background requests, account system or listening port. All other operation remains offline.

## Four bays

| Bay | Initial drill |
| --- | --- |
| 01 Precision | Stationary targets that respawn at seeded positions after a hit |
| 02 Tracking | Moving targets for continuous crosshair tracking |
| 03 Reflex | Seeded appearing targets; a hit temporarily removes that target |
| 04 Cover | Reversing targets behind solid cover |

Each bay's activation zone includes its lane and the approach immediately in front of it in the common corridor. Only the occupied bay has live character targets: other bays contribute no character draws, character shadows, character hit queries or capture annotations. Crossing a zone boundary switches the target population immediately; leaving all zones removes every target instance. Inactive bays keep their settings, paused timer, target state and scores for your return. The character mesh and textures are shared and stay cached, so walking between bays does not reload the model from disk. Walls and cover block shots. Collision substeps prevent walking through walls during frame stalls, and diagonal movement slides along obstacles.

Targets use one shared frozen pose. Translation provides movement without continuously animating each skeleton. Up to 12 targets in the occupied bay are supported, and characters outside the camera's field of view are culled. Static walls, floors, rails and trim are combined into cached GPU meshes, with bay-level frustum culling. Inactive scoreboards refresh when their data changes. The world and view weapon share one scene pass and MSAA resolve; a separate clean-capture buffer is allocated only when first needed. Inactive windows and menus render at 30 FPS; active practice respects the selected frame limit. Left-button press events are counted so separate short clicks in one frame are retained; holding the button does not create extra shots.

## Shoot the controls

Approach a bay's illuminated panel and aim at a tile. The tile lights up under the crosshair. Shoot **+**, **-**, a tab or an action. Panel interaction works from the front within 12 meters and respects obstacles. Control shots never add to the shot/hit counters.

- **DRILL:** motion pattern, target count, peak speed, depth past the firing line, lateral spread, timed duration, deterministic seed and cover.
- **TARGET:** height, facing, frozen pose, pose frame, reference aim-point height, appearance period, mixed stationary targets and optional markers. Pose and pose frame apply to every bay because geometry is shared; other target values belong to that bay.
- **OPTICS:** horizontal FOV, mouse sensitivity, FPS limit, raw mouse input, crosshair, view weapon, neutral/warm/dim lighting and minimal HUD. These camera/display preferences apply everywhere.
- **INPUT:** your mouse DPI, centimeters per full turn, raw input, preparation countdown, Keep Target/Respawn behavior, respawn delay and anti-aliasing. DPI, sensitivity and anti-aliasing apply everywhere; countdown and target response belong to the selected bay. Returning to DRILL only changes the page.

**START TIMED** resets that bay, gives you three seconds to turn toward the targets, then starts its timer. INPUT can change this preparation period to 0–5 seconds. Preparation pauses in the menu and excludes shots from scoring. The same tile becomes **STOP + SAVE**. The timer also saves automatically on completion. **RESET** starts free practice again; **DEFAULTS** restores that bay's drill and target values. A setting change ends and saves any affected timed drill first. Resizing the viewport saves and ends timed runs so their viewing conditions remain consistent.

The physical scoreboard shows time, hits, accuracy, time on a character mesh, and either median acquisition time or best hit streak for the Tracking bay. Results remain after completion; firing again begins a fresh free-practice score. Only your active bay scores hits. Shooting another bay's target leaves its paused/completed score intact; move into that bay to practice there. A shot into scenery is a miss for your current bay.

Set DPI to match your mouse's current hardware/software setting. The suite does not detect or change device DPI. Adjusting centimeters per turn enables raw input when supported; the displayed distance assumes the declared DPI is correct. This is a physical turn-distance calculation, not a guarantee that another game's aiming or FOV matches.

The selected quality preset controls MSAA on the actual offscreen scene and view weapon, with a single-sample fallback when allocation is unavailable. INPUT can separately disable anti-aliasing; reselecting a quality preset restores its setting. Active-bay characters and cover cast shadows on Mid or higher. The HUD reports frame time and FPS; neither measures input-to-display latency.

Cyan, blue, violet and pink accents give the range a Vapor Lock / vaporwave appearance. Rails, ceiling strips, sign bars, cover trim and panel frames have soft local glow; menu borders and selected controls use restrained edge halos. Text, scores and the VL wordmark remain unglowed. The effect uses emissive geometry and depth-tested accent strips, with no full-screen bloom or additional render textures.

Depth is measured beyond the colored firing line. Floor plates mark 10, 20 and 30 meters from that line. Your actual distance to a target changes as you walk.

## Characters

Use the arrows in Esc to switch between the embedded **SWAT.glb** and supported local replacements. Place self-contained `.glb` files in **models** beside the EXE, then reopen Esc. No restart is required. Custom files take precedence over a built-in character with the same filename; unique filenames are recommended. The original asset location is still recognized for existing users. An invalid replacement selected in Esc preserves the currently loaded model. The view-only prop uses original procedural C++ geometry.

This folder contains 3D target characters, not `.onnx` detection networks. The suite supplies rendered scenes for separate detector testing; it does not execute or connect to an aiming-assistance program. **README.txt** inside models contains the accepted-format instructions.

Replacement requirements: glTF 2.0 binary, embedded buffers and PNG/JPEG textures, opaque triangle meshes with normals, and no required extensions, Draco compression or morph targets. Each mesh primitive must fit 16-bit vertex indexing. Use at most four bone influences per vertex and bake the base-color texture into the first UV set without texture-transform extensions. Linear/step animation clips can supply a frozen pose; cubic spline animation is rejected. External file references, network paths and linked locations outside the suite are rejected.

The renderer supports base colors, embedded textures and single-/double-sided materials with lightweight diffuse lighting. It does not implement full glTF physically based material rendering. Transparent/cutout surfaces are rejected because the hit tests measure opaque geometry.

SWAT is the Quaternius CC0 character. The view-only prop is original procedural geometry and is excluded from clean captures. The portable EXE includes the full project license and retained third-party credits in **LICENSES.txt**, created beside it on first launch. See **THIRD_PARTY_NOTICES.md** and **LICENSE** in this source tree.

## Measurements and local files

Every completed timed drill writes matching **range-*.json**, **range-*.csv**, **range-*-shots.csv** and **range-*.ini** files. Schema-4 JSON includes model-content/configuration identifiers, starting resolution, shot accuracy, hits per second, best hit streak, successful target-ID switches, acquisition samples, time on mesh, mean angular distance to the nearest visible unobstructed reference point, frame-time percentiles and discarded stall time. Unobserved accuracy, on-target percentage, frame percentiles, angular error and acquisition median are `null`, not perfect zero. Per-shot CSV records simulation time, target ID, result and hit position.

Acquisition is scene time from the reference point becoming visible to its first successful hit in that visibility episode. It is not a biological reaction-time score; repeated hits on an already acquired target do not add another sample. Comparison IDs group equal initial conditions, including model bytes, pose, drill settings, optics, input, display preferences, actual MSAA/shadow/raw-input availability and resolution. They are not security checksums or proof of an identical camera/shot sequence. This rendering revision uses a new comparison version so it does not group with older renders.

Target simulation uses a fixed 120 Hz clock. Measurements integrate render-frame samples, and camera/stat samples are recorded at up to 30 Hz; short contacts between frames may be missed. Fractional durations end at the first tick at or after the deadline. Menus, loss of focus and time spent in another bay do not advance the drill.

A geometric hit uses the posed character triangles, including carried equipment. A spatial index accelerates these queries. There is no damage model, aim correction, automatic shooting, input injection or connection to an assistance program.

**F12** writes **capture-*.png** and **capture-*.json** using only the application's offscreen render texture. Capture schema 2 now identifies the occupied bay and excludes inactive bay targets. It includes model-content ID, pose, light, actual MSAA samples, camera parameters, character positions, projected bounds and whether the reference aim point is obstructed by range geometry. Bounds crossing the near plane are clipped before projection. Bounds are not pixel segmentation and may include occluded body parts. Screen HUD and the view weapon are excluded; physical signs/panels remain in the scene. This suite does not run an ONNX detector or establish BO7 detector compatibility.

**settings.ini** preserves shared preferences. **range-bay-1.ini** through **range-bay-4.ini** preserve independent bay settings after normal use. Validation runs do not change these preferences. **runtime-*.log** records each process's startup and shutdown separately. All runtime assets, settings, reports and logs remain inside the suite.

## Build and verify

For development, install CMake 3.25+ and Visual Studio Desktop C++ Build Tools, then run **build.ps1**. It builds the portable native executable and runs the core tests. **package.ps1** builds and copies the EXE into the release output, with its hash recorded in **portable-package.json**. Only **Aim Testing Suite.exe** needs distribution; the toolchain and build scripts are not needed by users. No Python or browser runtime is involved. Earlier one-time character conversion scripts are not used by the application.

Additional native validation commands:

~~~powershell
& '.\Aim Testing Suite.exe' --range-check SWAT.glb
& '.\Aim Testing Suite.exe' --startup-check
& '.\Aim Testing Suite.exe' --fps-check max
& '.\Aim Testing Suite.exe' --fps-check high
& '.\Aim Testing Suite.exe' --fps-check mid
& '.\Aim Testing Suite.exe' --fps-check potato
~~~

The range check runs hidden for 192 frames at 1920 × 1080, renders all bays and menu/panel states, exercises controls, raycasts, reports, model replacement, collision, timers, countdown, respawning, calibration, graphics allocation, all quality presets, dropdown picking, click-event gates and the website action, then exits. It does not launch a browser. Its simulation uses controlled test timing, not a hardware FPS benchmark. The startup check briefly shows only its own window without requesting foreground focus, verifies visibility and exits. These commands generate local reports; a successful source export is not evidence that a graphical check or FPS benchmark has run.

Each FPS check uses the shipping renderer at **1920 × 1080, 100% render scale**, with the frame limiter off and swap interval zero requested. It warms up each bay for two seconds, then measures eight seconds of moving targets and a slow camera sweep. It takes no screenshots, does not capture mouse input, does not save preferences and exits automatically. Client, framebuffer, the scene texture and any allocated capture texture must match 1920 × 1080 or the check fails. A test window can extend beyond a smaller display's work area; normal gameplay windows remain resizable. Reports are **fps-1080p-max.json**, **fps-1080p-high.json**, **fps-1080p-mid.json** and **fps-1080p-potato.json**. Average FPS is total frames divided by measured wall time; driver/compositor policy and other system load can still affect results.

Source, executable and settings were backed up locally before each major pass. Existing conversion assets and user-added files were retained. The original Vapor Lock branding palette is retained using native drawing; no website runtime or another local project's source is included.

The optional **--profile-check mid** command runs the same 1080p scene sweep with host-stage timers and asynchronous GPU timer queries. Its **profile-1080p-mid.json** output is diagnostic; ordinary FPS runs leave profiling off. **RENDER_OPTIMIZATION.md** records the rendering changes and before/after results.
