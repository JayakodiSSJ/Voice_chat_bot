// AvatarModel.js
import { useGLTF } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useRef } from "react";

const AvatarModel = ({ visemeIndex }) => {
  const { scene, nodes } = useGLTF("/models/Femalev1.glb"); //WaitressCH,Avatar.glb
  const meshRef = useRef();
  const targetInfluences = useRef([]);
  const currentInfluences = useRef([]);

  useEffect(() => {
    const mesh = Object.values(nodes).find((node) => node.morphTargetInfluences);
    if (mesh && mesh.morphTargetInfluences) {
      meshRef.current = mesh;
      const numInfluences = mesh.morphTargetInfluences.length;
      targetInfluences.current = new Array(numInfluences).fill(0);
      currentInfluences.current = new Array(numInfluences).fill(0);
      mesh.morphTargetInfluences = currentInfluences.current;
    }
  }, [nodes]);

  useEffect(() => {
    if (meshRef.current && visemeIndex >= 0 && visemeIndex < targetInfluences.current.length) {
      targetInfluences.current = targetInfluences.current.map(() => 0);
      targetInfluences.current[visemeIndex] = 1;
    }
  }, [visemeIndex]);

  useFrame((state, delta) => {
    if (meshRef.current && meshRef.current.morphTargetInfluences) {
      const speed = 5; // Adjust this for smoothness vs responsiveness
      for (let i = 0; i < targetInfluences.current.length; i++) {
        currentInfluences.current[i] += (targetInfluences.current[i] - currentInfluences.current[i]) * speed * delta;
        currentInfluences.current[i] = Math.max(0, Math.min(1, currentInfluences.current[i]));
      }
      meshRef.current.morphTargetInfluences = currentInfluences.current;
    }
  });

  return (
    <primitive
      object={scene}
      scale={[2, 2, 2]}
      position={[0, -106, 0]}
    />
  );
};

export default AvatarModel;
