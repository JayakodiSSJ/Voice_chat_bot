import { useMemo } from "react";
import * as THREE from "three";

// Ported from Voice_Aisle (app/components/voice/avatar/useMorphTargets.tsx), TS types stripped.
export function useMorphTargets(scene, morphMeshes) {
  const setMorph = (name, value) => {
    const aliases = {
      mouthOpen: ["jawOpen", "mouthOpen", "viseme_aa", "viseme_O"],
      eyeBlinkLeft: ["eyeBlinkLeft", "eye_blink_left", "eyesClosed", "Eye_Blink_Left"],
      eyeBlinkRight: ["eyeBlinkRight", "eye_blink_right", "eyesClosed", "Eye_Blink_Right"],
      mouthSmile: ["mouthSmile", "mouth_smile", "Mouth_Smile"],
      browInnerUp: ["browInnerUp", "brow_inner_up", "Brow_Inner_Up"],
      browDownLeft: ["browDownLeft", "brow_down_left", "Brow_Down_Left"],
      browDownRight: ["browDownRight", "brow_down_right", "Brow_Down_Right"],
      mouthLeft: ["mouthLeft", "mouth_left", "mouthStretchLeft", "viseme_I"],
      mouthRight: ["mouthRight", "mouth_right", "mouthStretchRight"],
      mouthClose: ["mouthClose", "mouthRollLower", "mouth_close", "viseme_PP"],
      mouthFunnel: ["mouthFunnel", "mouth_funnel", "viseme_U", "viseme_W"],
      mouthPucker: ["mouthPucker", "mouth_pucker", "viseme_OO"],
      mouthUpperUpLeft: ["mouthUpperUpLeft", "upperLip", "mouthShrugUpper", "viseme_E"],
      mouthLowerDownLeft: ["mouthLowerDownLeft", "lowerLip", "mouthShrugLower"],
      mouthDimpleLeft: ["mouthDimpleLeft", "mouthDimple", "mouth_dimple_left"],
      mouthDimpleRight: ["mouthDimpleRight", "mouth_dimple_right"],
      mouthPressLeft: ["mouthPressLeft", "jawForward", "mouth_press_left"],
      mouthPressRight: ["mouthPressRight", "mouth_press_right"],
      cheekPuff: ["cheekPuff", "cheek_puff", "cheeks_puff"],
      cheekSquintLeft: ["cheekSquintLeft", "cheek_squint_left", "cheekRaiseLeft"],
      cheekSquintRight: ["cheekSquintRight", "cheek_squint_right", "cheekRaiseRight"],
      noseSneerLeft: ["noseSneerLeft", "nose_sneer_left"],
      noseSneerRight: ["noseSneerRight", "nose_sneer_right"],
      browOuterUpLeft: ["browOuterUpLeft", "brow_outer_up_left"],
      browOuterUpRight: ["browOuterUpRight", "brow_outer_up_right"],
      eyeWideLeft: ["eyeWideLeft", "eye_wide_left", "Eye_Wide_Left"],
      eyeWideRight: ["eyeWideRight", "eye_wide_right", "Eye_Wide_Right"],
      eyeSquintLeft: ["eyeSquintLeft", "eye_squint_left"],
      eyeSquintRight: ["eyeSquintRight", "eye_squint_right"],
      mouthRollLower: ["mouthRollLower", "mouth_roll_lower", "lowerLipRoll", "viseme_TH"],
      mouthRollUpper: ["mouthRollUpper", "mouth_roll_upper", "upperLipRoll"],
      mouthUpperUpRight: ["mouthUpperUpRight", "upperLipRight", "mouthShrugUpperRight"],
      mouthLowerDownRight: ["mouthLowerDownRight", "lowerLipRight", "mouthShrugLowerRight"],
    };
    const targets = aliases[name] ?? [name];
    for (const mesh of morphMeshes) {
      if (!mesh.morphTargetInfluences || !mesh.morphTargetDictionary) continue;
      if (name === "mouthSmile") {
        const left = mesh.morphTargetDictionary.mouthSmileLeft;
        const right = mesh.morphTargetDictionary.mouthSmileRight;
        if (left !== undefined || right !== undefined) {
          if (left !== undefined) mesh.morphTargetInfluences[left] = THREE.MathUtils.clamp(value, 0, 1);
          if (right !== undefined) mesh.morphTargetInfluences[right] = THREE.MathUtils.clamp(value, 0, 1);
          continue;
        }
      }
      for (const t of targets) {
        const idx = mesh.morphTargetDictionary[t];
        if (idx !== undefined) {
          mesh.morphTargetInfluences[idx] = THREE.MathUtils.clamp(value, 0, 1);
          break;
        }
      }
    }
  };

  const findBone = useMemo(() => {
    const map = {};
    scene.traverse((obj) => {
      if (obj.isBone) map[obj.name] = obj;
    });
    return (keywords) => {
      for (const kw of keywords) {
        const key = Object.keys(map).find((n) => n.toLowerCase().includes(kw.toLowerCase()));
        if (key) return map[key];
      }
      return null;
    };
  }, [scene]);

  return { setMorph, findBone };
}

export default useMorphTargets;
