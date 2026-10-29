# Privacy in the community source snapshot

VaporLock's publisher licensing, device activation, telemetry, update checks,
forced-update policy and hosted model catalog are retired in this source tree.
The client does not require a VaporLock account or license key.

Capture frames, models, datasets and profiles are processed locally by default.
Explicit optional integrations, including Ultralytics, use the third-party
service you select and may transmit selected data to that service. Review its
terms and credentials before using it. Local API access remains opt-in and keeps
its authentication and transport protections.

Dependency setup can download packages from their publishers when requested.
This is distinct from publisher updates or telemetry. User-requested external
links open in a browser. No universal no-network or invisibility claim is made
for third-party dependencies or a modified build.

Personal profiles, datasets, model weights, external API credentials, prior
activation records, operator secrets and customer/licensing records are not
included in the public source export. Historical operator records remain
private and are not published or silently destroyed by this transition.
