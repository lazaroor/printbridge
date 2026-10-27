"""Conexão local (LAN) com impressoras Bambu Lab via MQTT."""

import json
import ssl
import threading
import time

import paho.mqtt.client as mqtt

# Modelos cuja câmera é RTSP (porta 322). Os demais (P1P, P1S, A1, A1 mini) usam JPEG na porta 6000.
RTSP_MODELS = {"X1", "X1C", "X1E", "H2D", "H2S", "P2S"}


def deep_merge(dst: dict, src: dict) -> None:
    """A série P1/A1 manda só o que mudou; juntamos no estado acumulado."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value


class BambuPrinter:
    marca = "bambu"
    # o que o painel pode oferecer para esta marca (Creality/Klipper têm menos — ver creality.py/klipper.py)
    recursos = ["camera", "luz", "temperaturas", "velocidade", "pular", "lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.serial = cfg["serial"]
        self.access_code = cfg["access_code"]
        self.model = cfg.get("model", "P1S").upper().replace(" ", "")
        camera = cfg.get("camera", "auto")
        if camera == "auto":
            camera = "rtsp" if self.model in RTSP_MODELS else "jpeg"
        self.camera_type = camera

        self.connected = False
        self.sem_conexao_desde = time.time()  # usado para procurar a impressora se o IP mudar
        self.last_update = 0.0
        self.resposta_impressao = None  # última resposta ao comando project_file
        self.state: dict = {}
        self._lock = threading.Lock()
        self._seq = 0

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"farm-{self.id}-{int(time.time())}",
        )
        self._client.username_pw_set("bblp", self.access_code)
        # A impressora usa certificado próprio (autoassinado), então não validamos.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self._client.tls_set_context(ctx)
        self._client.reconnect_delay_set(min_delay=2, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # ---------- conexão ----------

    def start(self) -> None:
        self._client.connect_async(self.ip, 8883, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[{self.name}] falha ao conectar: {reason_code} (confira IP, serial e access code)")
            return
        print(f"[{self.name}] conectada ({self.ip})")
        self.connected = True
        self.sem_conexao_desde = None
        client.subscribe(f"device/{self.serial}/report")
        # Pede o estado completo uma vez; depois chegam só as mudanças.
        self._publish({"pushing": {"command": "pushall"}})

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected:
            print(f"[{self.name}] desconectada ({reason_code}), tentando de novo...")
            self.sem_conexao_desde = time.time()
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        resp = data.get("print") if isinstance(data.get("print"), dict) else None
        if resp and resp.get("command") == "project_file":
            self.resposta_impressao = resp  # a impressora respondeu ao "imprimir arquivo"
        with self._lock:
            deep_merge(self.state, data)
            self.last_update = time.time()

    def _publish(self, payload: dict) -> None:
        self._seq += 1
        for section in payload.values():
            section.setdefault("sequence_id", str(self._seq))
        self._client.publish(f"device/{self.serial}/request", json.dumps(payload))

    # ---------- comandos ----------

    def command(self, action: str, value=None) -> None:
        if action == "set_nozzle":
            self._gcode(f"M104 S{int(value)}")
        elif action == "set_bed":
            self._gcode(f"M140 S{int(value)}")
        elif action == "set_speed":
            # 1 silencioso · 2 padrão · 3 esportivo · 4 ludicrous (mesmo seletor do Bambu Studio)
            if int(value) not in (1, 2, 3, 4):
                raise ValueError("velocidade deve ser 1 a 4")
            self._publish({"print": {"command": "print_speed", "param": str(int(value))}})
        elif action == "skip":
            # pular peças: identify_id de cada uma (slice_info do arquivo — objetos.py)
            ids = [int(v) for v in (value or [])]
            if not ids:
                raise ValueError("nenhuma peça para pular")
            self._publish({"print": {"command": "skip_objects", "obj_list": ids}})
        elif action in ("pause", "resume", "stop"):
            self._publish({"print": {"command": action, "param": ""}})
        elif action in ("light_on", "light_off"):
            self._publish({"system": {
                "command": "ledctrl",
                "led_node": "chamber_light",
                "led_mode": "on" if action == "light_on" else "off",
                "led_on_time": 500, "led_off_time": 500,
                "loop_times": 0, "interval_time": 0,
            }})
        elif action == "refresh":
            self._publish({"pushing": {"command": "pushall"}})
        else:
            raise ValueError(f"ação desconhecida: {action}")

    def _gcode(self, line: str) -> None:
        self._publish({"print": {"command": "gcode_line", "param": line + " \n"}})

    def imprimir(self, nome_arquivo: str, placa: int, slots: list[int], titulo: str) -> None:
        """Começa a imprimir um .gcode.3mf que JÁ está na raiz do cartão SD (envio.py).

        slots: para cada filamento do projeto (na ordem do Bambu Studio), o slot que vai usar —
        0..3 no AMS lite/AMS 1 (4..7 no 2º AMS…), 254 = bobina externa, -1 = não usado.
        """
        externo = all(s in (254, -1) for s in slots)
        # X1/H2 leem do cartão por file://; P1/A1 aceitam o caminho do FTP (onde o arquivo foi gravado)
        url = f"file:///sdcard/{nome_arquivo}" if self.model.startswith(("X1", "H2")) else f"ftp:///{nome_arquivo}"
        cmd = {
            "command": "project_file",
            "param": f"Metadata/plate_{int(placa)}.gcode",
            "url": url,
            "subtask_name": titulo,
            "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
            "md5": "",
            "timelapse": False,
            "bed_type": "auto",
            "bed_levelling": True,
            "flow_cali": False,
            "vibration_cali": False,
            "layer_inspect": False,
            "use_ams": not externo,
        }
        if not externo:
            cmd["ams_mapping"] = [int(s) for s in slots]
        self.resposta_impressao = None
        self._publish({"print": cmd})

    # ---------- estado resumido para o painel ----------

    def summary(self) -> dict:
        with self._lock:
            p = dict(self.state.get("print", {}))
        lights = {l.get("node"): l.get("mode") for l in p.get("lights_report", []) or []}
        remaining = p.get("mc_remaining_time")  # minutos
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "marca": self.marca,
            "recursos": self.recursos,
            "connected": self.connected,
            "last_update": self.last_update,
            "status": p.get("gcode_state", "UNKNOWN"),
            "job": p.get("subtask_name") or p.get("gcode_file") or "",
            "progress": p.get("mc_percent"),
            "remaining_min": remaining,
            "layer": p.get("layer_num"),
            "total_layers": p.get("total_layer_num"),
            "nozzle_temp": p.get("nozzle_temper"),
            "nozzle_target": p.get("nozzle_target_temper"),
            "bed_temp": p.get("bed_temper"),
            "bed_target": p.get("bed_target_temper"),
            "chamber_temp": p.get("chamber_temper"),
            "speed_level": p.get("spd_lvl"),
            "error_code": p.get("print_error") or 0,
            "light": lights.get("chamber_light"),
            "sdcard": p.get("sdcard"),
            "arquivo": p.get("gcode_file") or "",  # para o "Pular peças" saber qual arquivo ler
            "pulados": [int(x) for x in (p.get("s_obj") or []) if str(x).lstrip("-").isdigit()],
            "filamentos": filamentos(p),
        }


def filamentos(p: dict) -> list[dict]:
    """O que está carregado: slots do AMS / AMS lite (0..3 no primeiro, 4..7 no segundo…) e a
    bobina externa (slot 254). Slot vazio fica de fora."""
    lista = []
    for ams in (p.get("ams") or {}).get("ams") or []:
        for t in ams.get("tray") or []:
            if t.get("tray_type"):
                lista.append({"slot": int(ams.get("id", 0)) * 4 + int(t.get("id", 0)), "fonte": "ams",
                              "tipo": t["tray_type"], "cor": (t.get("tray_color") or "")[:6]})
    vt = p.get("vt_tray") or {}
    lista.append({"slot": 254, "fonte": "externo", "tipo": vt.get("tray_type") or "", "cor": (vt.get("tray_color") or "")[:6]})
    return lista
