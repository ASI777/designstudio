import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { MechanicalViewMode } from "./contracts";
import { MechanicalScene } from "./MechanicalScene";

export class MechanicalViewer {
  readonly scene = new THREE.Scene();
  readonly mechanical = new MechanicalScene(this.scene);
  private readonly renderer: THREE.WebGLRenderer;
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly loader = new GLTFLoader();
  private readonly resizeObserver: ResizeObserver;
  private frame = 0;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene.background = new THREE.Color(0x20242b);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100_000);
    this.camera.position.set(120, 100, 120);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;

    this.scene.add(new THREE.HemisphereLight(0xdde7ff, 0x202020, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 2.5);
    key.position.set(100, 160, 120);
    this.scene.add(key);
    this.scene.add(new THREE.GridHelper(200, 20, 0x4e5663, 0x303641));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    this.resize();
    this.animate();
  }

  async loadGlb(url: string): Promise<void> {
    const loaded = await this.loader.loadAsync(url);
    this.mechanical.replaceAssembly(loaded.scene);
    this.fitToAssembly();
  }

  setMode(mode: MechanicalViewMode): void {
    this.mechanical.setMode(mode);
  }

  setExplode(amount: number): void {
    this.mechanical.setExplode(amount);
  }

  fitToAssembly(): void {
    const bounds = this.mechanical.bounds();
    if (bounds.isEmpty()) return;
    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() * 0.65, 1);
    this.controls.target.copy(center);
    this.camera.position.copy(center).add(new THREE.Vector3(radius, radius * 0.8, radius));
    this.camera.near = Math.max(radius / 10_000, 0.001);
    this.camera.far = Math.max(radius * 20, 100);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  dispose(): void {
    cancelAnimationFrame(this.frame);
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
  }

  private resize(): void {
    const width = Math.max(1, this.canvas.clientWidth);
    const height = Math.max(1, this.canvas.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  private animate = (): void => {
    this.frame = requestAnimationFrame(this.animate);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  };
}

