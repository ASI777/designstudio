import * as THREE from "three";
import type { MechanicalViewMode } from "./contracts";
import { applyExplode, applyViewMode } from "./viewModes";

export class MechanicalScene {
  readonly root = new THREE.Group();
  private mode: MechanicalViewMode = "manufacturing_internals";
  private selected: THREE.Object3D | null = null;

  constructor(public readonly scene: THREE.Scene) {
    this.root.name = "DesignStudioMechanicalAssembly";
    scene.add(this.root);
  }

  replaceAssembly(assembly: THREE.Object3D): void {
    this.root.clear();
    this.root.add(assembly);
    applyViewMode(this.root, this.mode);
  }

  setMode(mode: MechanicalViewMode): void {
    this.mode = mode;
    applyViewMode(this.root, mode);
  }

  setExplode(amount: number): void {
    applyExplode(this.root, amount);
  }

  selectSemanticId(semanticId: string | null): void {
    if (this.selected) this.selected.userData.selected = false;
    this.selected = null;
    if (!semanticId) return;
    this.root.traverse((node) => {
      if (this.selected || node.userData.semanticId !== semanticId) return;
      this.selected = node;
      node.userData.selected = true;
    });
  }

  bounds(): THREE.Box3 {
    return new THREE.Box3().setFromObject(this.root);
  }
}

