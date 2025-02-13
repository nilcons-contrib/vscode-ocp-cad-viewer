import orjson
import socket
import yaml
from pathlib import Path
from flask import Flask, render_template
from flask_sock import Sock
from simple_websocket import ConnectionClosed
from ocp_vscode.comms import MessageType
from ocp_vscode.backend import ViewerBackend
from ocp_vscode.backend_logo import logo
from ocp_vscode.state import resolve_path

CONFIG_FILE = "~/.ocpvscode_standalone"

DEFAULTS = {
    "debug": False,
    "no_glass": False,
    "no_tools": False,
    "tree_width": 240,
    "theme": "light",
    "control": "trackball",
    "modifier_keys": {
        "shift": "shiftKey",
        "ctrl": "ctrlKey",
        "meta": "metaKey",
    },
    "new_tree_behavior": True,
    "pan_speed": 0.5,
    "rotate_speed": 1.0,
    "zoom_speed": 0.5,
    "axes": False,
    "axes0": False,
    "grid_xy": False,
    "grid_yz": False,
    "grid_xz": False,
    "perspective": False,
    "transparent": False,
    "black_edges": False,
    "collapse": "R",
    "reset_camera": "RESET",
    "up": "Z",
    "ticks": 10,
    "center_grid": False,
    "default_opacity": 0.5,
    "explode": False,
    "default_edgecolor": "#808080",
    "default_color": "#e8b024",
    "default_thickedgecolor": "MediumOrchid",
    "default_facecolor": "Violet",
    "default_vertexcolor": "MediumOrchid",
    "angular_tolerance": 0.2,
    "deviation": 0.1,
    "ambient_intensity": 1.0,
    "direct_intensity": 1.1,
    "metalness": 0.3,
    "roughness": 0.65,
}

SCRIPTS = """
    <script type="module" src="static/js/three-cad-viewer.esm.js"></script>
    <script type="module" srv="static/js/comms.js"></script>
    <script type="module" srv="static/js/logo.js"></script>
"""

JS = "./static/js/three-cad-viewer.esm.js"
CSS = "./static/css/three-cad-viewer.css"

STATIC = """
        import { Comms } from "./static/js/comms.js";
        import { logo } from "./static/js/logo.js";
"""


def COMMS(host, port):
    return f"""
        const comms = new Comms("{host}", {port});
        const vscode = {{postMessage: (msg) => {{
                comms.sendStatus(msg);
            }}
        }};
        const standaloneViewer = () => {{
            const ocpLogo = JSON.parse(logo);
            decode(ocpLogo);
            
            viewer = showViewer(ocpLogo.data.shapes, ocpLogo.config);
        }}
        window.showViewer = standaloneViewer;
    """


INIT = """onload="showViewer()" """


class Viewer:
    def __init__(self, params):
        self.status = {}
        self.config = {}
        self.debug = params.get("debug", False)
        self.params = params

        self.configure(self.params)

        self.app = Flask(__name__)
        self.sock = Sock(self.app)

        self.backend = ViewerBackend(self.port)

        self.python_client = None
        self.javascript_client = None
        self.splash = True

        self.sock.route("/")(self.handle_message)
        self.app.add_url_rule("/viewer", "viewer", self.index)

        # get ip address of current machine
        if params.get("host") == "127.0.0.1":
            self.ip_address = "127.0.0.1"
        elif params.get("host") == "0.0.0.0":
            hostname = socket.gethostname()
            self.ip_address = socket.gethostbyname(hostname)
        else:
            self.ip_address = params.get("host")

    def debug_print(self, *msg):
        if self.debug:
            print("Debug:", *msg)

    def configure(self, params):
        # Start with defaults
        local_config = DEFAULTS.copy()

        # Then apply everything from the config file if it exists
        config_file = Path(resolve_path(CONFIG_FILE))
        if config_file.exists():
            with open(config_file, "r") as f:
                defaults = yaml.safe_load(f)
                for k, v in defaults.items():
                    local_config[k] = v

        local_config = dict(sorted(local_config.items()))
        print("\nlocal:config (1):", local_config)
        print("\nparams", params)
        # Get all params != their default value and apply it
        grid = [
            local_config["grid_xy"],
            local_config["grid_yz"],
            local_config["grid_xz"],
        ]
        for k, v in params.items():
            if k == "port":
                self.port = v
            elif k == "host":
                self.host = v
            elif k not in ["create_configfile"]:
                if v != local_config.get(k):
                    if k == "grid_xy":
                        grid[0] = True
                    elif k == "grid_yz":
                        grid[1] = True
                    elif k == "grid_xz":
                        grid[2] = True
                    else:
                        local_config[k] = v
        local_config["grid"] = grid
        local_config["reset_camera"] = local_config["reset_camera"].upper()

        local_config = dict(sorted(local_config.items()))

        for k, v in local_config.items():
            if k in ["grid_xy", "grid_yz", "grid_xz"]:
                continue
            if k == "collapse":
                self.config["collapse"] = str(v)
            elif k == "no_glass":
                self.config["glass"] = not v
            elif k == "no_tools":
                self.config["tools"] = not v
            elif k == "perspective":
                self.config["ortho"] = not v

            else:
                self.config[k] = v

        self.debug_print("\nConfig:", self.config)

    def start(self):
        self.app.run(debug=self.debug, port=self.port, host=self.host)
        self.sock.init_app(self.app)
        self.backend.load_model(logo)

    def index(self):
        return render_template(
            "viewer.html",
            standalone_scripts=SCRIPTS,
            standalone_imports=STATIC,
            standalone_comms=COMMS(self.ip_address, self.port),
            standalone_init=INIT,
            styleSrc=CSS,
            scriptSrc=JS,
            treeWidth=self.config["tree_width"],
            **self.config,
        )

    def not_registered(self):
        print(
            "\nNo browser registered. Please open the viewer in a browser or refresh the viewer page\n"
        )

    def handle_message(self, ws):

        while True:
            data = ws.receive()
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            message_type = data[0]
            data = data[2:]

            if message_type == "C":
                self.python_client = ws
                cmd = orjson.loads(data)
                if cmd == "status":
                    self.debug_print("Received status command")
                    self.python_client.send(orjson.dumps({"text": self.status}))
                elif cmd == "config":
                    self.debug_print("Received config command")
                    self.configure(self.params)
                    self.config["_splash"] = self.splash
                    self.python_client.send(orjson.dumps(self.config))
                elif cmd.type == "screenshot":
                    self.debug_print("Received screenshot command")
                    self.python_client(orjson.dumps(cmd))

            elif message_type == "D":
                self.python_client = ws
                self.debug_print("Received a new model")
                if self.javascript_client is None:
                    self.not_registered()
                    continue
                self.javascript_client.send(data)
                if self.splash:
                    self.splash = False

            elif message_type == "U":
                self.javascript_client = ws
                changes = orjson.loads(data)["text"]
                self.debug_print("Received incremental UI changes", changes)
                for key, value in changes.items():
                    self.status[key] = value
                self.backend.handle_event(changes, MessageType.UPDATES)

            elif message_type == "S":
                self.python_client = ws
                self.debug_print("Received a config")
                if self.javascript_client is None:
                    self.not_registered()
                    continue
                self.javascript_client.send(data)
                self.debug_print("Posted config to view")

            elif message_type == "L":
                self.javascript_client = ws
                print("\nBrowser as viewer client registered\n")

            elif message_type == "B":
                model = orjson.loads(data)["model"]
                self.backend.handle_event(model, MessageType.DATA)
                self.debug_print("Model data sent to the backend")

            elif message_type == "R":
                self.python_client = ws
                if self.javascript_client is None:
                    self.not_registered()
                    continue
                self.javascript_client.send(data)
                self.debug_print("Backend response received.", data)
