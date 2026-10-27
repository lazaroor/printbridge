/* PrintBridge para outros sites e sistemas.

   Embutir o painel (iframe que ajusta a altura sozinho):
     <div id="impressoras"></div>
     <script src="http://SERVIDOR:8750/printbridge.js"></script>
     <script>
       PrintBridge.embutir("#impressoras", { servidor: "http://SERVIDOR:8750", token: "TOKEN",
                                             impressora: "ID (opcional)", controles: true, tema: "claro" });
     </script>

   Usar a API direto (o site precisa estar em Configurações → Sites que podem embutir):
     const pb = PrintBridge.cliente("http://SERVIDOR:8750", "TOKEN");
     await pb.impressoras();                       // estado de todas
     await pb.comando(id, "pause");                // pause, resume, stop, set_nozzle, set_bed, set_speed, light_on, light_off
     const parar = pb.ouvir((lista) => { ... });   // atualizações a cada 2 s; parar() encerra
     pb.camera(id)                                 // endereço do vídeo MJPEG, para um <img src>
*/
(function () {
  "use strict";

  function embutir(alvo, opcoes) {
    const o = opcoes || {};
    const lugar = typeof alvo === "string" ? document.querySelector(alvo) : alvo;
    if (!lugar) throw new Error("PrintBridge.embutir: elemento não encontrado");
    const servidor = String(o.servidor || "").replace(/\/$/, "");
    const q = new URLSearchParams();
    if (o.token) q.set("token", o.token);
    if (o.impressora) q.set("impressora", o.impressora);
    if (o.controles === false) q.set("controles", "0");
    if (o.tema) q.set("tema", o.tema);
    const quadro = document.createElement("iframe");
    quadro.src = `${servidor}/embed?${q}`;
    quadro.title = o.titulo || "PrintBridge";
    quadro.style.cssText = "width:100%;border:0;display:block;height:" + (o.altura || 480) + "px";
    quadro.setAttribute("allow", "clipboard-write");
    const origem = new URL(servidor || location.href).origin;
    window.addEventListener("message", (e) => {
      if (e.origin === origem && e.source === quadro.contentWindow && e.data && e.data.printbridge === "altura" && !o.altura) {
        quadro.style.height = Math.max(120, Math.ceil(e.data.altura)) + "px";
      }
    });
    lugar.replaceChildren(quadro);
    return quadro;
  }

  function cliente(servidor, token) {
    const base = String(servidor || "").replace(/\/$/, "");
    const cab = (json) => Object.assign({ Authorization: "Bearer " + token }, json ? { "Content-Type": "application/json" } : {});
    async function pedir(caminho, metodo, corpo) {
      const r = await fetch(base + "/api/" + caminho, { method: metodo || "GET", headers: cab(corpo !== undefined),
        body: corpo === undefined ? undefined : JSON.stringify(corpo) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.erro || "erro " + r.status);
      return d;
    }
    return {
      impressoras: () => pedir("impressoras"),
      impressora: (id) => pedir("impressoras/" + id),
      comando: (id, acao, valor) => pedir(`impressoras/${id}/comando`, "POST", { acao, valor }),
      camera: (id) => `${base}/api/impressoras/${id}/camera.mjpeg?token=${encodeURIComponent(token)}`,
      foto: (id) => `${base}/api/impressoras/${id}/camera.jpg?token=${encodeURIComponent(token)}&t=${Date.now()}`,
      ouvir(aoMudar) {
        const fonte = new EventSource(`${base}/api/eventos?token=${encodeURIComponent(token)}`);
        fonte.addEventListener("impressoras", (e) => aoMudar(JSON.parse(e.data)));
        return () => fonte.close();
      },
    };
  }

  window.PrintBridge = { embutir, cliente };
})();
