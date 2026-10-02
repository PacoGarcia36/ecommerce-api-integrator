"""
Genera el refresh_token de Google Ads via OAuth2 (flujo loopback moderno).

Reemplaza al viejo flujo OOB (urn:ietf:wg:oauth:2.0:oob), que Google
deshabilitó. Usa InstalledAppFlow.run_local_server: levanta un servidor
local que captura el código automáticamente — solo hay que autorizar en
el navegador, sin pegar códigos a mano.

Uso:
    python get_refresh_token.py [ruta_al_client_secret.json]

Si no se pasa ruta, busca el client_secret_*.json más reciente en
C:\\Users\\<usuario>\\Downloads.

Preserva developer_token y login_customer_id del google-ads.yaml existente;
solo actualiza client_id, client_secret y refresh_token.
"""
import os
import sys
import glob
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES    = ["https://www.googleapis.com/auth/adwords"]
YAML_PATH = os.path.join(os.path.dirname(__file__), "google-ads.yaml")


def find_client_json() -> str:
    """Ruta del client JSON: argumento CLI o el más reciente en Downloads."""
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if not os.path.isfile(path):
            sys.exit(f"ERROR: no existe el archivo {path}")
        return path

    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    matches = glob.glob(os.path.join(downloads, "client_secret_*.json"))
    if not matches:
        sys.exit(f"ERROR: no encontré ningún client_secret_*.json en {downloads}")
    newest = max(matches, key=os.path.getmtime)
    print(f"Usando client JSON más reciente:\n  {newest}\n")
    return newest


def read_existing_yaml_fields() -> dict:
    """Rescata developer_token y login_customer_id del YAML actual."""
    fields = {"developer_token": None, "login_customer_id": None}
    if not os.path.isfile(YAML_PATH):
        return fields
    with open(YAML_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            for key in fields:
                if line.startswith(key + ":"):
                    fields[key] = line.split(":", 1)[1].strip().strip("'\"")
    return fields


def write_yaml(client_id: str, client_secret: str, refresh_token: str,
               developer_token: str, login_customer_id: str) -> None:
    content = (
        f"developer_token: {developer_token}\n"
        f"client_id: {client_id}\n"
        f"client_secret: {client_secret}\n"
        f"refresh_token: {refresh_token}\n"
        f"login_customer_id: '{login_customer_id}'\n"
        f"use_proto_plus: True\n"
    )
    with open(YAML_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\ngoogle-ads.yaml actualizado en: {YAML_PATH}")


def main():
    client_json = find_client_json()

    with open(client_json, encoding="utf-8") as f:
        installed = json.load(f).get("installed", {})
    client_id     = installed.get("client_id", "")
    client_secret = installed.get("client_secret", "")
    if not client_id or not client_secret:
        sys.exit("ERROR: el JSON no parece un OAuth client de tipo 'installed'.")

    existing = read_existing_yaml_fields()
    developer_token   = existing["developer_token"] or os.environ.get(
        "GOOGLE_ADS_DEVELOPER_TOKEN", "")
    login_customer_id = existing["login_customer_id"] or os.environ.get(
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")

    if not developer_token:
        print("AVISO: no encontré developer_token en el YAML ni en el entorno; "
              "quedará vacío y tendrás que completarlo a mano.")

    print("=" * 62)
    print("  OBTENCIÓN DE REFRESH TOKEN — GOOGLE ADS (flujo loopback)")
    print("=" * 62)
    print("\nSe abrirá el navegador. Iniciá sesión con la cuenta que")
    print("administra el MCC y autorizá el acceso. El código se captura")
    print("solo — no tenés que pegar nada.\n")

    flow = InstalledAppFlow.from_client_secrets_file(client_json, scopes=SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",   # fuerza la emisión del refresh_token
    )

    if not creds.refresh_token:
        sys.exit("ERROR: la respuesta no incluyó refresh_token. "
                 "Probá de nuevo revocando el acceso previo en "
                 "https://myaccount.google.com/permissions")

    print(f"\nrefresh_token obtenido:\n  {creds.refresh_token}")

    write_yaml(client_id, client_secret, creds.refresh_token,
               developer_token, login_customer_id)

    print("\n" + "=" * 62)
    print("  Listo. Cargá este refresh_token (y el client_id/secret nuevos)")
    print("  en los GitHub Secrets del repo de pipelines.")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
