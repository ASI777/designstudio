import * as THREE from "three";
import {OrbitControls} from "/vendor/OrbitControls.js";
import {GLTFLoader} from "/vendor/GLTFLoader.js";

const viewport=document.querySelector("#viewport");
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.outputColorSpace=THREE.SRGBColorSpace;
viewport.appendChild(renderer.domElement);
const scene=new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xffffff,0x26313d,2.2));
const key=new THREE.DirectionalLight(0xffffff,3); key.position.set(4,5,6); scene.add(key);
const rim=new THREE.DirectionalLight(0x5b9cff,2); rim.position.set(-5,2,-4); scene.add(rim);
const grid=new THREE.GridHelper(10,20,0x3a4652,0x202830); scene.add(grid);
const camera=new THREE.PerspectiveCamera(42,1,.001,1000); camera.position.set(2.5,1.8,3.2);
const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=true;
let catalog=null,selected=null,mode="generated",model=null,wireframe=false;
const loader=new GLTFLoader();

function resize(){const w=viewport.clientWidth,h=viewport.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix()}
new ResizeObserver(resize).observe(viewport);
function frame(object){const box=new THREE.Box3().setFromObject(object),size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());const radius=Math.max(size.x,size.y,size.z);controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(radius*1.6,radius*1.1,radius*1.8));camera.near=Math.max(radius/1000,.0001);camera.far=Math.max(radius*100,10);camera.updateProjectionMatrix();controls.update();grid.position.y=box.min.y}
function clear(){if(!model)return;scene.remove(model);model.traverse(o=>{if(o.geometry)o.geometry.dispose();if(o.material){const a=Array.isArray(o.material)?o.material:[o.material];a.forEach(m=>m.dispose())}});model=null}
function setText(id,value){document.querySelector(id).textContent=value??"—"}
function loadModel(){clear();if(!selected)return;const url=mode==="cad"?selected.cad_url:selected.generated_url;if(!url)return;loader.load(url,g=>{model=g.scene;model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;o.material.wireframe=wireframe}});scene.add(model);frame(model)},undefined,e=>console.error(e))}
function verdict(value){return value==null?"—":value?"PASS":"FAIL"}
function select(slug){selected=catalog.products.find(p=>p.slug===slug);document.querySelectorAll(".product").forEach(x=>x.classList.toggle("active",x.dataset.slug===slug));setText("#title",selected.label);const source=selected.omni_generation_succeeded===false?"Shape fallback · Omni no-surface":selected.omni_generation_succeeded?"Omni refined":"Shape bootstrap";setText("#meta",`${selected.image_count} supplied views · seed ${selected.seed} · ${source}`);setText("#status",selected.status);setText("#vertices",selected.vertices);setText("#faces",selected.faces);const d=selected.deviation||{};setText("#median",d.median_mm==null?"—":`${d.median_mm.toFixed(3)} mm`);setText("#p95",d.p95_mm==null?"—":`${d.p95_mm.toFixed(3)} mm`);setText("#maximum",d.maximum_mm==null?"—":`${d.maximum_mm.toFixed(3)} mm`);setText("#ap242",verdict(selected.ap242_roundtrip_valid));setText("#shell",verdict(selected.valid_outer_shell));setText("#release",verdict(selected.release_eligible));document.querySelector("#images").innerHTML=selected.images.map(x=>`<img src="${x}" title="${x.split("/").pop()}">`).join("");loadModel()}
function renderCatalog(){const list=document.querySelector("#products");list.innerHTML=catalog.products.map(p=>`<button class="product" data-slug="${p.slug}"><span class="dot ${p.status}"></span>${p.label}<small>${p.status} · ${p.image_count} views</small></button>`).join("");list.querySelectorAll("button").forEach(b=>b.onclick=()=>select(b.dataset.slug));if(!selected&&catalog.products.length)select(catalog.products[0].slug);else if(selected)select(selected.slug)}
async function refresh(){try{const response=await fetch("/api/catalog",{cache:"no-store"});catalog=await response.json();document.querySelector("#connection").textContent=`Live · ${new Date().toLocaleTimeString()}`;renderCatalog()}catch(e){document.querySelector("#connection").textContent="Disconnected"}}
document.querySelectorAll("[data-mode]").forEach(b=>b.onclick=()=>{mode=b.dataset.mode;document.querySelectorAll("[data-mode]").forEach(x=>x.classList.toggle("active",x===b));loadModel()});
document.querySelector("#wireframe").onclick=()=>{wireframe=!wireframe;document.querySelector("#wireframe").classList.toggle("active",wireframe);if(model)model.traverse(o=>{if(o.isMesh)o.material.wireframe=wireframe})};
function animate(){requestAnimationFrame(animate);controls.update();renderer.render(scene,camera)} resize();animate();refresh();setInterval(refresh,5000);
