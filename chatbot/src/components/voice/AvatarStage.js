import React, { Suspense } from "react";
import { Canvas } from "@react-three/fiber";
import { ContactShadows } from "@react-three/drei";
import AvatarMesh from "./avatar/AvatarMesh";
import "./AvatarStage.css";

const SOUND_BAR_HEIGHTS = [4, 8, 14, 20, 16, 10, 6, 12, 18, 14, 8, 4];

class AvatarCanvasErrorBoundary extends React.Component {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    console.error("3D avatar failed to render:", error);
  }

  render() {
    if (this.state.hasError) {
      return <div className="voice-avatar-canvas-error" role="status">3D avatar unavailable</div>;
    }
    return this.props.children;
  }
}

function StatusLabel({ state, msg }) {
  return (
    <div className="voice-status-label">
      <span className={`voice-status-dot voice-status-dot--${state}`} />
      <span className={`voice-status-text voice-status-text--${state}`}>{msg}</span>
    </div>
  );
}

function SoundWaves({ state }) {
  if (state !== "speaking" && state !== "recording") return null;
  return (
    <div className={`voice-sound-waves voice-sound-waves--${state}`}>
      {SOUND_BAR_HEIGHTS.map((h, i) => (
        <div
          key={i}
          className="voice-sound-bar"
          style={{
            height: `${h}px`,
            animationDuration: `${0.4 + (i % 3) * 0.15}s`,
            animationDelay: `${i * 0.05}s`,
            opacity: 0.7 + (i % 2) * 0.3,
          }}
        />
      ))}
    </div>
  );
}

// state: 'idle' | 'recording' | 'thinking' | 'speaking'
export default function AvatarStage({ state, statusMsg, audioLevelRef, greeting = false }) {
  return (
    <div className={`voice-avatar-stage voice-avatar-stage--${state}`}>
      <div className="voice-avatar-glow-wrap">
        <div className="voice-avatar-glow" />
      </div>

      <AvatarCanvasErrorBoundary>
      <Canvas
        camera={{ position: [0, 1.45, 5.2], fov: 35 }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
        style={{ background: "transparent", width: "100%", height: "100%" }}
        onCreated={({ camera }) => camera.lookAt(0, 1.45, 3)}
      >
        <hemisphereLight args={["#e7edff", "#493b52", 0.55]} />
        <ambientLight intensity={0.25} />
        <directionalLight position={[1, 2, 2]} intensity={1} color="#fff5e8" />
        <directionalLight position={[-1, 0, 1]} intensity={0.3} color="#9bb8ff" />
        <directionalLight position={[0, -1, 0]} intensity={0.08} color="#ffc0a0" />
        <directionalLight position={[0, 2, -3]} intensity={0.18} color="#c6a0ff" />
        <Suspense fallback={null}>
          <AvatarMesh state={state} audioLevelRef={audioLevelRef} greeting={greeting} />
          <ContactShadows position={[0, -1.6, 0]} opacity={0.3} scale={1} blur={1} />
        </Suspense>
      </Canvas>
      </AvatarCanvasErrorBoundary>

      <div className="voice-avatar-hud">
        <SoundWaves state={state} />
        <StatusLabel state={state} msg={statusMsg} />
      </div>
    </div>
  );
}
