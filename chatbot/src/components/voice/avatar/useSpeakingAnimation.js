import { useRef } from "react";
import * as THREE from "three";

export function useSpeakingAnimation() {
  const blinkTimer = useRef(0);
  const nextBlink = useRef(2.5 + Math.random() * 3);
  const blinkValue = useRef(0);
  const blinkClosing = useRef(false);
  const smoothedAudio = useRef(0);
  const gazeTimer = useRef(0);
  const nextGazeShift = useRef(2 + Math.random() * 2);
  const gazeX = useRef(0);
  const gazeY = useRef(0);
  const gazeTargetX = useRef(0);
  const gazeTargetY = useRef(0);

  const runFrame = (time, delta, setMorph, headRotX, headRotY, audioLevel, state, greeting) => {
    const dt = Math.min(delta, 0.05);

    blinkTimer.current += dt;
    if (blinkTimer.current >= nextBlink.current && !blinkClosing.current) {
      blinkClosing.current = true;
    }
    const blinkTarget = blinkClosing.current ? 1 : 0;
    blinkValue.current = THREE.MathUtils.damp(blinkValue.current, blinkTarget, blinkClosing.current ? 32 : 20, dt);
    if (blinkClosing.current && blinkValue.current > 0.92) {
      blinkClosing.current = false;
      blinkTimer.current = 0;
      nextBlink.current = 2.5 + Math.random() * 3.8;
    }
    setMorph("eyeBlinkLeft", blinkValue.current);
    setMorph("eyeBlinkRight", blinkValue.current * 0.96);

    gazeTimer.current += dt;
    if (gazeTimer.current >= nextGazeShift.current) {
      gazeTimer.current = 0;
      nextGazeShift.current = 2 + Math.random() * 3;
      gazeTargetX.current = (Math.random() - 0.5) * 0.008;
      gazeTargetY.current = (Math.random() - 0.5) * 0.006;
    }
    gazeX.current = THREE.MathUtils.damp(gazeX.current, gazeTargetX.current, 7, dt);
    gazeY.current = THREE.MathUtils.damp(gazeY.current, gazeTargetY.current, 7, dt);
    setMorph("eyeLookOutLeft", Math.max(gazeX.current, 0));
    setMorph("eyeLookInLeft", Math.max(-gazeX.current, 0));
    setMorph("eyeLookInRight", Math.max(gazeX.current, 0));
    setMorph("eyeLookOutRight", Math.max(-gazeX.current, 0));
    setMorph("eyeLookDownLeft", Math.max(-gazeY.current, 0));
    setMorph("eyeLookUpLeft", Math.max(gazeY.current, 0));
    setMorph("eyeLookDownRight", Math.max(-gazeY.current, 0));
    setMorph("eyeLookUpRight", Math.max(gazeY.current, 0));

    const speakingLevel = state === "speaking" ? THREE.MathUtils.clamp(audioLevel, 0, 1) : 0;
    smoothedAudio.current = THREE.MathUtils.damp(smoothedAudio.current, speakingLevel, 18, dt);
    setMorph("mouthOpen", Math.max(0, smoothedAudio.current - 0.035) * 0.9);
    const isGreeting = state === "speaking" && greeting;
    setMorph("mouthSmile", isGreeting ? 0.2 : state === "recording" ? 0.08 : state === "speaking" ? 0.08 : 0.035);
    setMorph("cheekSquintLeft", isGreeting ? 0.08 : state === "speaking" ? 0.03 : 0);
    setMorph("cheekSquintRight", isGreeting ? 0.08 : state === "speaking" ? 0.03 : 0);
    setMorph("browInnerUp", state === "recording" ? 0.045 : isGreeting ? 0.035 : 0);
    setMorph("browOuterUpLeft", state === "recording" ? 0.02 : 0);
    setMorph("browOuterUpRight", state === "recording" ? 0.02 : 0);
    setMorph("browDownLeft", state === "thinking" ? 0.07 : 0);
    setMorph("browDownRight", state === "thinking" ? 0.07 : 0);

    const listeningTilt = state === "recording" ? 0.012 : 0;
    const thinkingTilt = state === "thinking" ? -0.015 : 0;
    headRotX.current = THREE.MathUtils.damp(
      headRotX.current,
      Math.sin(time * 0.58) * 0.004 + listeningTilt + thinkingTilt,
      3,
      dt
    );
    headRotY.current = THREE.MathUtils.damp(
      headRotY.current,
      Math.sin(time * 0.37) * 0.004,
      2.5,
      dt
    );
  };

  return { runFrame };
}

export default useSpeakingAnimation;
