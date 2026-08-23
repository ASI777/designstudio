import "./style.css";
import { MechanicalViewer } from "./MechanicalViewer";
import type { MechanicalViewMode } from "./contracts";

const canvas = document.querySelector<HTMLCanvasElement>("#mechanical-canvas");
const mode = document.querySelector<HTMLSelectElement>("#view-mode");
const explode = document.querySelector<HTMLInputElement>("#explode");
const status = document.querySelector<HTMLElement>("#status");
if (!canvas || !mode || !explode || !status) throw new Error("viewer controls are missing");

const viewer = new MechanicalViewer(canvas);
mode.addEventListener("change", () => viewer.setMode(mode.value as MechanicalViewMode));
explode.addEventListener("input", () => viewer.setExplode(Number(explode.value)));

const glbUrl = new URLSearchParams(window.location.search).get("glb");
if (glbUrl) {
  viewer.loadGlb(glbUrl)
    .then(() => { status.textContent = "Approved assembly loaded"; })
    .catch((error: unknown) => {
      status.textContent = `Model could not be loaded: ${error instanceof Error ? error.message : "unknown error"}`;
    });
}

