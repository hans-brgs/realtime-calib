// Operator-facing mirror convention: every camera view the operator watches while
// aiming a board — live tiles and recorded replays alike — is flipped horizontally,
// so moving the board left in front of the lens moves it left on screen (the webcam /
// selfie behaviour operators expect).
//
// DISPLAY ONLY. This is a CSS transform on the <video> element and nothing else:
// detection, intrinsic/extrinsic compute and the bundle adjustment all run in the
// calibration-service on the NATIVE frames written to disk, never on the pixels the
// browser paints. Recorded videos, corner data and the exported TOML are unaffected.
// Never mirror by flipping frames server-side — that would change what calibration
// sees. Burn-in overlays are composited into the preview frame upstream (ADR-0003),
// so they flip together with the image and stay aligned on the board.
export const PREVIEW_MIRROR = 'scaleX(-1)';
