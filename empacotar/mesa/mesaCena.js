import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

/* Prévia 3D da mesa no "Pular peças" — three.js puro, fora do React (como suporteCena.js).

   Cada peça chega como as paredes externas do G-code, camada por camada (agente/trajetos.py):
   [camada, z, [x, y, x, y…]] em décimos de mm, no sistema da impressora (origem na frente à
   esquerda, Y para o fundo). Na cena: X da tela = X da mesa, Y = altura, Z = frente da mesa,
   com o centro da mesa na origem — a vista "frente" é a de quem está diante da impressora.

   O que já foi impresso (camada ≤ camada atual) sai sólido; o resto, apagado. Clicar numa peça
   chama aoClicar(id); passar o mouse, aoPassar(id | null). Para acertar o clique sem caçar uma
   linha de 1 px, cada peça tem uma caixa invisível do tamanho dela. */

const COR = {
  fundo: 0xeef2ee,
  mesa: 0x2a2e2b,
  grade: 0x3d443f,
  peca: 0xf2f4f1,
  marcada: 0x2fbf71,
  pulada: 0xe5484d,
  cima: 0x8fd3ff,
};

const VISTAS = { diagonal: [0.85, 0.8, 0.9], frente: [0, 0.45, 1], cima: [0, 1, 0.001], lado: [1, 0.45, 0.001] };

/* A mesa num canvas: o topo do canvas é o FUNDO (a textura entra no plano deitado com o
   +Y local virado para o fundo da cena) e a esquerda do canvas é o X = 0 da impressora. */
function texturaMesa(W, H, borda) {
  const ppm = Math.min(6, 2048 / (Math.max(W, H) + 2 * borda)); // pixels por mm
  const c = document.createElement("canvas");
  c.width = Math.round((W + 2 * borda) * ppm);
  c.height = Math.round((H + 2 * borda) * ppm);
  const g = c.getContext("2d");
  const px = (mm) => mm * ppm;
  g.fillStyle = "#c9d0cb"; // borda clara
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#f08c2b"; // faixa da frente
  g.fillRect(0, px(borda + H), c.width, px(borda));
  g.fillStyle = "#" + COR.mesa.toString(16).padStart(6, "0");
  g.fillRect(px(borda), px(borda), px(W), px(H));
  g.strokeStyle = "#" + COR.grade.toString(16).padStart(6, "0");
  g.lineWidth = Math.max(1, ppm * 0.4);
  for (let x = 0; x <= W; x += 32) { g.beginPath(); g.moveTo(px(borda + x), px(borda)); g.lineTo(px(borda + x), px(borda + H)); g.stroke(); }
  for (let y = 0; y <= H; y += 32) { g.beginPath(); g.moveTo(px(borda), px(borda + H - y)); g.lineTo(px(borda + W), px(borda + H - y)); g.stroke(); }
  const texto = (t, x, y, ang, cor) => {
    g.save();
    g.translate(px(x), px(y));
    g.rotate(ang);
    g.fillStyle = cor;
    g.font = `800 ${px(borda * 0.5)}px system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(t, 0, 0);
    g.restore();
  };
  texto("FRENTE  ·  lado da tela", borda + W / 2, borda * 1.5 + H, 0, "#ffffff");
  texto("FUNDO", borda + W / 2, borda / 2, 0, "#3d4540");
  texto("ESQUERDA", borda / 2, borda + H / 2, -Math.PI / 2, "#3d4540");
  texto("DIREITA", borda * 1.5 + W, borda + H / 2, Math.PI / 2, "#3d4540");
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

export function criarMesa(canvas, { mesa = [256, 256], vista, aoClicar, aoPassar, aoGirar } = {}) {
  const [W, H] = mesa;
  const cena = new THREE.Scene();
  cena.background = new THREE.Color(COR.fundo);

  const camera = new THREE.PerspectiveCamera(36, 1, 1, 5000);
  const render = new THREE.WebGLRenderer({ canvas, antialias: true });
  render.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const orbita = new OrbitControls(camera, canvas);
  orbita.enableDamping = true;
  orbita.dampingFactor = 0.12;
  orbita.maxPolarAngle = Math.PI * 0.49; // não passa por baixo da mesa

  // mesa: placa escura com grade a cada 32 mm (como a PEI texturizada) e, em volta, uma borda
  // com FRENTE / FUNDO / ESQUERDA / DIREITA — a frente (lado da tela da impressora) em laranja.
  // Fica na borda, fora da placa, para nenhuma peça cobrir o texto.
  const borda = Math.max(16, Math.max(W, H) * 0.09);
  const placa = new THREE.Mesh(new THREE.PlaneGeometry(W + 2 * borda, H + 2 * borda),
    new THREE.MeshBasicMaterial({ map: texturaMesa(W, H, borda) }));
  placa.rotation.x = -Math.PI / 2;
  cena.add(placa);

  // peças: um par de LineSegments por peça (impresso / ainda não) + caixa invisível para o clique.
  // Enquanto o arquivo ainda vem do cartão, a vista chega aos poucos (de baixo para cima):
  // atualizarVista() troca as peças sem mexer na câmera de quem está olhando.
  let pecas = new Map(); // id → { feito, falta, caixa, linhas }
  let caixas = [], linhas = [];
  let altura = 10;
  const centro = new THREE.Vector3();
  let alcance = Math.max(W, H) * 0.6;
  let enquadrado = false;
  function montarPecas(v) {
    for (const p of pecas.values()) {
      cena.remove(p.feito, p.falta, p.caixa);
      p.feito.geometry.dispose(); p.falta.geometry.dispose(); p.caixa.geometry.dispose();
      p.feito.material.dispose(); p.falta.material.dispose(); p.caixa.material.dispose();
    }
    pecas = new Map();
    const tudo = new THREE.Box3();
    for (const [id, ls] of Object.entries(v?.pecas || {})) {
      const caixa3 = new THREE.Box3();
      for (const [, z, xy] of ls) {
        for (let i = 0; i < xy.length; i += 2) caixa3.expandByPoint(new THREE.Vector3(xy[i] / 10 - W / 2, z, H / 2 - xy[i + 1] / 10));
      }
      if (caixa3.isEmpty()) continue;
      tudo.union(caixa3);
      altura = Math.max(altura, caixa3.max.y);
      const feito = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: COR.peca }));
      const falta = new THREE.LineSegments(new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({ color: COR.peca, transparent: true, opacity: 0.22, depthWrite: false }));
      const tam = caixa3.getSize(new THREE.Vector3()).max(new THREE.Vector3(2, 2, 2));
      const caixa = new THREE.Mesh(new THREE.BoxGeometry(tam.x, tam.y, tam.z),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
      caixa3.getCenter(caixa.position);
      caixa.userData.id = feito.userData.id = falta.userData.id = Number(id);
      cena.add(feito, falta, caixa);
      pecas.set(Number(id), { feito, falta, caixa, linhas: ls });
    }
    caixas = [...pecas.values()].map((x) => x.caixa);
    linhas = [...pecas.values()].flatMap((x) => [x.feito, x.falta]);
    // enquadra as peças (não a mesa inteira): numa A1 com 5 peças no meio, a mesa toda as deixaria
    // minúsculas. Só na primeira vez: depois a câmera é de quem está olhando.
    if (!enquadrado && !tudo.isEmpty()) {
      tudo.getCenter(centro);
      const t = tudo.getSize(new THREE.Vector3());
      alcance = Math.min(Math.max(W, H) * 0.6, Math.max(45, t.x, t.y, t.z) * 0.95);
      centro.y = altura * 0.4;
      enquadrado = true;
    }
  }
  montarPecas(vista);

  const separar = (linhas, camadaAtual) => {
    const sim = [], nao = [];
    for (const [cam, z, xy] of linhas) {
      const alvo = camadaAtual == null || cam <= camadaAtual ? sim : nao;
      for (let i = 0; i + 3 < xy.length; i += 2) {
        alvo.push(xy[i] / 10 - W / 2, z, H / 2 - xy[i + 1] / 10, xy[i + 2] / 10 - W / 2, z, H / 2 - xy[i + 3] / 10);
      }
    }
    return [sim, nao];
  };

  let camadaFeita, ultimoEstado = {};
  function atualizarVista(v) {
    montarPecas(v);
    camadaFeita = Symbol("refazer"); // força remontar o "já impresso / ainda não" de todas
    atualizar(ultimoEstado);
  }
  function atualizar(estado = {}) {
    ultimoEstado = estado;
    const { marcadas = new Set(), pulados = new Set(), camadaAtual = null, sobre = null } = estado;
    for (const [id, p] of pecas) {
      if (camadaAtual !== camadaFeita) {
        const [sim, nao] = separar(p.linhas, camadaAtual);
        p.feito.geometry.dispose();
        p.falta.geometry.dispose();
        p.feito.geometry = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(sim, 3));
        p.falta.geometry = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(nao, 3));
      }
      const cor = pulados.has(id) ? COR.pulada : marcadas.has(id) ? COR.marcada : sobre === id ? COR.cima : COR.peca;
      p.feito.material.color.setHex(cor);
      p.falta.material.color.setHex(cor);
      p.falta.material.opacity = marcadas.has(id) || sobre === id ? 0.45 : 0.22;
    }
    camadaFeita = camadaAtual;
  }

  function olhar(nome = "diagonal") {
    const d = new THREE.Vector3(...VISTAS[nome]).normalize();
    const raio = alcance * 2.2;
    orbita.target.copy(centro);
    camera.position.copy(centro).addScaledVector(d, raio);
    orbita.update();
  }

  // clique x arrastar: só conta como clique se o mouse quase não andou
  const raio = new THREE.Raycaster();
  const ponteiro = new THREE.Vector2();
  const acertar = (ev) => {
    const r = canvas.getBoundingClientRect();
    ponteiro.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    raio.setFromCamera(ponteiro, camera);
    // 1º: a linha mais próxima da câmera — é a peça que se vê sob o mouse, mesmo se a caixa
    // de outra a cobre (a base larga na frente do corpo, por exemplo). Folga de ~4 px.
    raio.params.Line.threshold = camera.position.distanceTo(orbita.target) * 0.008;
    const naLinha = raio.intersectObjects(linhas, false);
    if (naLinha.length) return naLinha[0].object.userData.id;
    // 2º: entre as linhas (o miolo da peça): a caixa mais justa sob o mouse
    const hits = raio.intersectObjects(caixas, false);
    if (!hits.length) return null;
    const volume = (h) => { const s = h.object.geometry.parameters; return s.width * s.height * s.depth; };
    return hits.slice(0, 3).sort((a, b) => volume(a) - volume(b))[0].object.userData.id;
  };
  let inicio = null;
  let ultimoSobre = null;
  const baixo = (ev) => { inicio = [ev.clientX, ev.clientY]; };
  const solta = (ev) => {
    if (inicio && Math.hypot(ev.clientX - inicio[0], ev.clientY - inicio[1]) < 5) {
      const id = acertar(ev);
      if (id != null) aoClicar?.(id);
    }
    inicio = null;
  };
  const move = (ev) => {
    if (inicio) return;
    const id = acertar(ev);
    canvas.style.cursor = id != null ? "pointer" : "grab";
    if (id !== ultimoSobre) { ultimoSobre = id; aoPassar?.(id); }
  };
  const sai = () => { if (ultimoSobre != null) { ultimoSobre = null; aoPassar?.(null); } };
  canvas.addEventListener("pointerdown", baixo);
  canvas.addEventListener("pointerup", solta);
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerleave", sai);

  const ajustar = () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    render.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const obs = new ResizeObserver(ajustar);
  obs.observe(canvas);
  ajustar();
  olhar();
  atualizar();

  let quadro;
  // bússola: para onde fica a FRENTE da mesa na tela (ângulo em graus, 0 = para baixo).
  // Quem usa (PularPecas) gira uma seta com isso — fica visível com qualquer zoom.
  const aqui = new THREE.Vector3(), frente = new THREE.Vector3();
  let anguloAnterior = null;
  const bussola = () => {
    if (!aoGirar) return;
    aqui.copy(orbita.target).project(camera);
    frente.copy(orbita.target).add(new THREE.Vector3(0, 0, H / 2)).project(camera);
    const dx = frente.x - aqui.x, dy = -(frente.y - aqui.y); // y da tela cresce para baixo
    const angulo = Math.round((Math.atan2(-dx, dy) * 180) / Math.PI);
    const deCima = camera.position.y - orbita.target.y > 0.97 * camera.position.distanceTo(orbita.target);
    if (angulo !== anguloAnterior) { anguloAnterior = angulo; aoGirar(angulo, deCima); }
  };
  const desenhar = () => {
    quadro = requestAnimationFrame(desenhar);
    orbita.update();
    bussola();
    render.render(cena, camera);
  };
  desenhar();

  return {
    atualizar,
    atualizarVista,
    olhar,
    destruir() {
      cancelAnimationFrame(quadro);
      obs.disconnect();
      canvas.removeEventListener("pointerdown", baixo);
      canvas.removeEventListener("pointerup", solta);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerleave", sai);
      orbita.dispose();
      cena.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
      render.dispose();
    },
  };
}
