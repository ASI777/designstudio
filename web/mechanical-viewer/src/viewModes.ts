import * as THREE from "three";
import type { MechanicalViewMode } from "./contracts";

type SemanticData = {
  designStudioRole?: string;
  semanticId?: string;
  clearanceClass?: string;
  explodeVector?: [number, number, number];
};

function semantic(node: THREE.Object3D): SemanticData {
  return node.userData as SemanticData;
}

function role(node: THREE.Object3D): string {
  const data = semantic(node);
  if (data.designStudioRole) return data.designStudioRole;
  const name = node.name.toLowerCase();
  if (name.includes("reservation") || name.includes("keepout")) return "reservation";
  if (name.includes("rib")) return "manufactured_rib";
  if (name.includes("comp") || name.startsWith("u") || name.startsWith("j")) return "component";
  if (name.includes("internal") || name.includes("frame")) return "internal_frame";
  return "exterior";
}

export function isVisibleInMode(node: THREE.Object3D, mode: MechanicalViewMode): boolean {
  const nodeRole = role(node);
  if (mode === "presentation") {
    return !["reservation", "component", "manufactured_rib", "internal_frame"].includes(nodeRole);
  }
  if (mode === "manufacturing_internals") {
    return nodeRole !== "reservation";
  }
  if (mode === "clearance") {
    return nodeRole !== "exterior";
  }
  return true;
}

export function applyViewMode(root: THREE.Object3D, mode: MechanicalViewMode): void {
  root.traverse((node) => {
    node.visible = isVisibleInMode(node, mode);
  });
}

export function applyExplode(root: THREE.Object3D, amount: number): void {
  const clamped = Math.max(0, Math.min(1, amount));
  root.traverse((node) => {
    const vector = semantic(node).explodeVector;
    if (!vector) return;
    node.position.set(vector[0] * clamped, vector[1] * clamped, vector[2] * clamped);
  });
}

