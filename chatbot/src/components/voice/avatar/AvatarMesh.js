import { useRef, useEffect, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import { useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { clone as cloneSkinnedScene } from "three/addons/utils/SkeletonUtils.js";
import { useMorphTargets } from "./useMorphTargets";
import { useSpeakingAnimation } from "./useSpeakingAnimation";

// Ported from Voice_Aisle (app/components/voice/avatar/AvatarMesh.tsx), TS types stripped.
const AVATAR_URL = "/models/agent_men.glb";

// state: 'idle' | 'recording' | 'thinking' | 'speaking'
export default function AvatarMesh({ state, audioLevelRef, greeting }) {
  const { scene: loadedScene, materials: loadedMaterials } = useGLTF(AVATAR_URL);
  const scene = useMemo(() => cloneSkinnedScene(loadedScene), [loadedScene]);

  const headRotX = useRef(0);
  const headRotY = useRef(0);

  const morphMeshes = useMemo(() => {
    const found = [];
    scene.traverse((obj) => {
      if (obj.isMesh && obj.morphTargetDictionary) found.push(obj);
    });
    return found;
  }, [scene]);

  const { setMorph, findBone } = useMorphTargets(scene, morphMeshes);
  const { runFrame } = useSpeakingAnimation();
  const head = findBone(["mixamorigHead", "head"]);
  const headRestRotation = useMemo(
    () => head?.quaternion.clone() ?? new THREE.Quaternion(),
    [head]
  );
  const gazeScratch = useMemo(
    () => ({
      headOffset: new THREE.Quaternion(),
      headEuler: new THREE.Euler(),
    }),
    []
  );

  useEffect(() => {
    headRotX.current = 0;
    headRotY.current = 0;

    let eyeMeshFound = false;
    let eyeTextureFound = false;
    let eyeUvFound = false;
    const palette = {
      Body: "#e9bca8",
      Head: "#f1c6ad",
      avaturn_hair_0_material: "#302039",
      avaturn_shoes_0_material: "#34436d",
      avaturn_look_0_material: "#5875c4",
    };
    scene.traverse((obj) => {
      if (!obj.isMesh || !obj.material) return;
      const sourceMaterials = Array.isArray(obj.material) ? obj.material : [obj.material];
      const materials = sourceMaterials.map((material) => material.clone());
      obj.material = Array.isArray(obj.material) ? materials : materials[0];

      if (obj.name === "Eye_Mesh") {
        eyeMeshFound = true;
        eyeUvFound = Boolean(obj.geometry?.attributes?.uv);
        const eyeTexture = loadedMaterials.Eyes?.map;
        materials.forEach((material) => {
          if (material.name !== "Eyes") return;
          if (!material.map && eyeTexture) material.map = eyeTexture;
          eyeTextureFound = Boolean(material.map);
          if (material.map) {
            material.map.colorSpace = THREE.SRGBColorSpace;
            material.map.needsUpdate = true;
          }
          material.color?.set("#ffffff");
          if (typeof material.metalness === "number") material.metalness = 0;
          if (typeof material.roughness === "number") material.roughness = 0.34;
          material.needsUpdate = true;
        });
      }

      materials.forEach((material) => {
        const tint = palette[material.name];
        if (tint && material.color) material.color.set(tint);
        if (material.map) {
          material.map.colorSpace = THREE.SRGBColorSpace;
          material.map.needsUpdate = true;
        }
        if (material.name === "EyeAO") {
          material.transparent = true;
          material.opacity = 0.1;
          material.depthWrite = false;
        }
        if (typeof material.metalness === "number") material.metalness = 0;
        if (material.name !== "Eyes" && typeof material.roughness === "number") {
          material.roughness = Math.max(material.roughness, 0.72);
        }
        material.needsUpdate = true;
      });
    });
    if (!eyeMeshFound || !eyeTextureFound || !eyeUvFound) {
      console.error("Avatar eye mesh is missing its mapped eye texture or UV coordinates.", {
        eyeMeshFound,
        eyeTextureFound,
        eyeUvFound,
      });
    }
  }, [loadedMaterials, scene]);

  useFrame(({ clock }, delta) => {
    const t = clock.elapsedTime;

    scene.position.y = -4.09 + Math.sin(t * 1.4) * 0.004;

    const leftArm = findBone(["LeftArm", "leftarm", "Left_Arm", "mixamorigLeftArm"]);
    const rightArm = findBone(["RightArm", "rightarm", "Right_Arm", "mixamorigRightArm"]);
    const leftFore = findBone(["LeftForeArm", "leftforearm", "Left_ForeArm", "mixamorigLeftForeArm"]);
    const rightFore = findBone(["RightForeArm", "rightforearm", "Right_ForeArm", "mixamorigRightForeArm"]);
    const leftHand = findBone(["LeftHand", "lefthand", "Left_Hand", "mixamorigLeftHand"]);
    const rightHand = findBone(["RightHand", "righthand", "Right_Hand", "mixamorigRightHand"]);

    if (leftArm) {
      leftArm.rotation.x = THREE.MathUtils.lerp(leftArm.rotation.x, 1.3, delta * 4);
      leftArm.rotation.z = THREE.MathUtils.lerp(leftArm.rotation.z, 0, delta * 4);
    }
    if (rightArm) {
      rightArm.rotation.x = THREE.MathUtils.lerp(rightArm.rotation.x, 1.3, delta * 4);
      rightArm.rotation.z = THREE.MathUtils.lerp(rightArm.rotation.z, 0, delta * 4);
    }
    if (leftFore) leftFore.rotation.x = THREE.MathUtils.lerp(leftFore.rotation.x, 0.15, delta * 4);
    if (rightFore) rightFore.rotation.x = THREE.MathUtils.lerp(rightFore.rotation.x, 0.15, delta * 4);
    if (leftHand) leftHand.rotation.x = THREE.MathUtils.lerp(leftHand.rotation.x, 0.2, delta * 4);
    if (rightHand) rightHand.rotation.x = THREE.MathUtils.lerp(rightHand.rotation.x, 0.2, delta * 4);

    runFrame(t, delta, setMorph, headRotX, headRotY, audioLevelRef?.current || 0, state, greeting);

    if (head) {
      gazeScratch.headEuler.set(headRotX.current, headRotY.current, 0, "YXZ");
      gazeScratch.headOffset.setFromEuler(gazeScratch.headEuler);
      head.quaternion.copy(headRestRotation).multiply(gazeScratch.headOffset);
    }

  });

  return <primitive object={scene} scale={3.2} position={[0, -4.09, 3]} />;
}

useGLTF.preload(AVATAR_URL);
