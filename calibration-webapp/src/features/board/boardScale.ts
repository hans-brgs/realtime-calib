import type { BoardTarget } from '@/transport/types';

/**
 * Which board on the Target Config screen carries the world's metric scale.
 *
 * The printed size feeds the EXTRINSIC solve alone: the intrinsic solve runs on
 * unit squares (the backend builds the ChArUco board with `squareLength=1.0`),
 * so scaling the target leaves the camera matrix and the distortion untouched.
 * A board therefore only sets the scale when it is the one the extrinsic step
 * uses — and the intrinsic board qualifies only when the extrinsic step
 * inherits it (`effective_extrinsic_board = extrinsic_board or intrinsic_board`).
 *
 * Two boards are on screen at once, and they answer this differently:
 *
 * | tab       | separate extrinsic board | edited | previewed |
 * | --------- | ------------------------ | ------ | --------- |
 * | intrinsic | no (inherits)            | yes    | yes       |
 * | intrinsic | yes                      | no     | no        |
 * | extrinsic | no (inherits)            | no     | yes       |
 * | extrinsic | yes                      | yes    | yes       |
 *
 * The third row is the subtle one: the extrinsic tab in inherit mode edits
 * nothing (it saves `board=null`), yet the preview shows the intrinsic board,
 * which does set the scale — so the size notice must stay assertive there.
 */
export function boardScaleRoles(
  active: BoardTarget,
  extrinsicDifferent: boolean,
): { edited: boolean; previewed: boolean } {
  return {
    edited: active === 'extrinsic' ? extrinsicDifferent : !extrinsicDifferent,
    previewed: !(active === 'intrinsic' && extrinsicDifferent),
  };
}
