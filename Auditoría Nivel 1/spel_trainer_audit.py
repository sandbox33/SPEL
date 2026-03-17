"""
SPEL — Auditor del Trainer (Paso 2 del plan S24)
Lee spel_trainer.py y reporta exactamente qué está mal y en qué línea.
No modifica nada. Solo diagnostica.

Uso en Colab:
    !python spel_trainer_audit.py --trainer /ruta/a/spel_trainer.py
    # o sin argumento (busca en rutas conocidas):
    !python spel_trainer_audit.py
"""

import os, re, ast, sys, argparse
from pathlib import Path
from datetime import datetime

# ── CONFIG ───────────────────────────────────────────────────────────────────
DRIVE = '/content/drive/MyDrive'
ROOT  = f'{DRIVE}/SPEL-v2.0'

TRAINER_SEARCH_PATHS = [
    f'{ROOT}/scripts/spel_trainer.py',
    f'{ROOT}/codigo/core/spel_trainer.py',
    f'{ROOT}/codigo/spel_trainer.py',
    f'/content/spel_trainer.py',
    f'/content/spel_root/spel_trainer.py',
]

# Features que DEBEN estar en el tensor (18 documentadas)
TENSOR_FEATURES_KNOWN = [
    'entropy_shannon', 'entropy_decay_lambda', 'entropy_psych_vix',
    'fibonacci_lag_1', 'fibonacci_lag_2', 'fibonacci_lag_3',
    'fibonacci_lag_5', 'fibonacci_lag_8', 'fibonacci_lag_13', 'fibonacci_lag_21',
    'goldstein_geo', 'n_events_ohlcv', 'vitality_tesla',
    'mass_panic_index', 'fear_momentum', 'vix_norm',
    'nash_frozen_7d', 'log_return',
]
TENSOR_INPUT_SIZE = 20  # R13

# Features que NO deben entrar al tensor (R13)
FORBIDDEN_IN_TENSOR = [
    'volume_type', 'asset_class', 'trading_session',  # metadata
    'goldstein_mean', 'tone_variance', 'zipf_concentration',  # GDELT extendido
    'open', 'close',  # excluidos explícitamente (son base del log_return)
]

# Patrones que indican lookahead en normalización (el más peligroso)
LOOKAHEAD_PATTERNS = [
    # Fit sobre dataset completo antes del split
    (r'scaler\.fit\s*\([^)]*\)\s*\n[^#]*train_test_split',
     "scaler.fit() ANTES del split — lookahead garantizado"),
    (r'StandardScaler\(\)\.fit_transform\s*\([^)]*X\b',
     "fit_transform sobre X completo — verificar que X ya es solo train"),
    (r'\.fit\(X\)',
     "fit sobre X — verificar que X es solo el set de entrenamiento"),
    (r'\.fit\(df\[',
     "fit sobre df completo — debe ser df_train"),
    (r'\.fit\(features\b',
     "fit sobre 'features' — verificar que no incluye val/test"),
    # std/mean global
    (r'df\[.+\]\.std\(\)',
     "std() sobre df completo — posible BUG-LA-01 (lookahead en normalización)"),
    (r'df\[.+\]\.mean\(\)',
     "mean() sobre df completo — verificar que es solo sobre train"),
]

# Patrones de split incorrecto
SPLIT_BAD_PATTERNS = [
    (r'train_test_split\s*\([^)]*shuffle\s*=\s*True',
     "shuffle=True en split temporal — lookahead garantizado"),
    (r'train_test_split\s*\([^)]*\)',
     "train_test_split sin shuffle=False — verificar que no hace shuffle"),
    (r'random_state',
     "random_state en split — confirmar que no hay shuffle temporal"),
]

# Patrones de loss reimplementada (riesgo de divergencia)
LOSS_REIMPL_PATTERNS = [
    (r'def\s+spel_loss',   "spel_loss definida inline en el trainer"),
    (r'def\s+asymmetric',  "loss asimétrica definida inline"),
    (r'0\.6\s*\*\s*dir_err', "reimplementación de la loss (0.6 * dir_err)"),
]

# Keys que DEBE tener el checkpoint guardado
CHECKPOINT_REQUIRED_KEYS = [
    'model_state_dict',
    'scaler',
    'sha_parquet',
    'p90_usado',
    'val_dir',
    'val_loss',
    'fecha',
    'asset',
]

# ── UTILIDADES ───────────────────────────────────────────────────────────────

def ok(msg):    print(f"    ✅  {msg}")
def warn(msg):  print(f"    ⚠️   {msg}")
def fail(msg):  print(f"    ❌  {msg}")
def info(msg):  print(f"    ℹ️   {msg}")
def head(msg):  print(f"\n  {'─'*55}\n  {msg}\n  {'─'*55}")

class Finding:
    def __init__(self, severity, point, line_no, line_text, detail, fix):
        self.severity  = severity   # 'BUG' | 'WARN' | 'INFO'
        self.point     = point      # 'P1'–'P5'
        self.line_no   = line_no
        self.line_text = line_text.strip()
        self.detail    = detail
        self.fix       = fix

findings = []

def add(severity, point, line_no, line_text, detail, fix):
    findings.append(Finding(severity, point, line_no, line_text, detail, fix))
    if severity == 'BUG':
        fail(f"L{line_no}: {detail}")
    elif severity == 'WARN':
        warn(f"L{line_no}: {detail}")
    else:
        info(f"L{line_no}: {detail}")

# ── ANÁLISIS ─────────────────────────────────────────────────────────────────

def find_trainer(override=None) -> str:
    if override and os.path.exists(override):
        return override
    for path in TRAINER_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


def audit_trainer(trainer_path: str):
    lines   = open(trainer_path).readlines()
    content = ''.join(lines)
    print(f"\n  Auditando: {trainer_path}")
    print(f"  Líneas: {len(lines)}")

    # ── PUNTO 1: Selección de features (tensor 20) ────────────────────────
    head("PUNTO 1 — Selección de features del tensor (input_size=20)")

    features_in_trainer = []
    for feat in TENSOR_FEATURES_KNOWN:
        for i, line in enumerate(lines, 1):
            if re.search(r'\b' + re.escape(feat) + r'\b', line):
                features_in_trainer.append(feat)
                break

    found_n = len(features_in_trainer)
    missing = [f for f in TENSOR_FEATURES_KNOWN if f not in features_in_trainer]

    info(f"Features conocidas del tensor encontradas en trainer: {found_n}/18")

    if missing:
        add('WARN', 'P1', 0, '',
            f"Features no encontradas en el trainer: {missing}",
            "Verificar si el trainer usa nombres distintos (alias) o si estas features faltan.")

    # Buscar las 2 features no documentadas
    info("Buscando las 2 features sin documentar (BUG-TENSOR-DOC)...")
    candidate_undoc = [
        'high_norm', 'low_norm', 'volume_norm', 'atr_norm', 'atr',
        'high', 'low', 'volume', 'range_norm', 'hl_range',
        'open_norm', 'close_norm',
    ]
    found_undoc = []
    for cand in candidate_undoc:
        for i, line in enumerate(lines, 1):
            # Buscar en contexto de selección de features (listas, tensores)
            if re.search(r'["\']' + re.escape(cand) + r'["\']', line):
                found_undoc.append((cand, i, line.strip()))
                break

    if found_undoc:
        for feat, lineno, linetext in found_undoc:
            add('INFO', 'P1', lineno, linetext,
                f"Feature candidate para tensor no documentada: '{feat}'",
                f"Confirmar si '{feat}' es una de las 2 features faltantes. "
                f"Si sí → añadir a TENSOR_FEATURES_KNOWN en el log v34.")
    else:
        add('WARN', 'P1', 0, '',
            "No se encontraron las 2 features sin documentar (BUG-TENSOR-DOC)",
            "Buscar manualmente dónde el trainer define la lista de columnas "
            "para el tensor. Buscar: 'FEATURES', 'cols', 'feature_cols', 'X_cols'.")

    # Verificar que FORBIDDEN_IN_TENSOR no aparecen en contexto de tensor
    for forbidden in FORBIDDEN_IN_TENSOR:
        for i, line in enumerate(lines, 1):
            if re.search(r'["\']' + re.escape(forbidden) + r'["\']', line):
                # Solo es un BUG si aparece en contexto de features del tensor
                context_keywords = ['feature', 'tensor', 'X', 'input', 'cols', 'FEAT']
                surrounding = ''.join(lines[max(0,i-3):i+3])
                if any(kw in surrounding for kw in context_keywords):
                    add('BUG', 'P1', i, line,
                        f"Feature prohibida en tensor: '{forbidden}' (R13)",
                        f"Remover '{forbidden}' de la selección de features. "
                        f"Esta columna no debe entrar al tensor LSTM.")

    # ── PUNTO 2: Normalización (lookahead) ───────────────────────────────
    head("PUNTO 2 — Normalización (BUG-LA-01 potencial)")

    found_scaler = False
    for i, line in enumerate(lines, 1):
        # Detectar dónde se hace el fit del scaler
        if 'scaler' in line.lower() or 'StandardScaler' in line or 'MinMaxScaler' in line:
            found_scaler = True
            if '.fit(' in line or '.fit_transform(' in line:
                # Verificar si el fit es ANTES o DESPUÉS del split
                # Buscamos el split temporal en las líneas anteriores
                prev_100_lines = ''.join(lines[max(0,i-100):i])
                split_done_before = any(kw in prev_100_lines for kw in [
                    'train', '2021', 'cutoff', 'date <=', 'iloc[:', 'split'
                ])
                if not split_done_before:
                    add('BUG', 'P2', i, line,
                        "scaler.fit() antes de que el split temporal sea visible en el código",
                        "Mover scaler.fit() a DESPUÉS del split. "
                        "El scaler SOLO debe ver datos de entrenamiento (≤ 2021-12-31). "
                        "Si ve val o test → BUG-LA-01 vuelve.")
                else:
                    ok(f"L{i}: scaler.fit() con split previo detectado")

    if not found_scaler:
        add('WARN', 'P2', 0, '',
            "No se detectó StandardScaler ni MinMaxScaler en el trainer",
            "El trainer puede estar normalizando de otra forma. "
            "Buscar: normalize, z_score, (x - mean) / std. "
            "Cualquier normalización con estadísticos globales es lookahead.")

    # Buscar std/mean globales (BUG-LA-01 directo)
    for i, line in enumerate(lines, 1):
        if re.search(r'df\[.+\]\.(std|mean)\(\)', line):
            add('BUG', 'P2', i, line,
                "std()/mean() sobre df completo — BUG-LA-01",
                "Calcular std/mean solo sobre df_train. "
                "Nunca sobre el dataset completo antes del split.")

    # ── PUNTO 3: Split temporal ───────────────────────────────────────────
    head("PUNTO 3 — Split temporal (anti-lookahead)")

    split_found = False
    date_splits = []
    for i, line in enumerate(lines, 1):
        # Buscar splits por fecha
        if re.search(r'202[0-3]', line) and any(kw in line for kw in
                     ['train', 'val', 'test', 'split', 'cutoff', 'date']):
            date_splits.append((i, line.strip()))
            split_found = True

        # Detectar shuffle explícito (FATAL)
        if 'shuffle' in line.lower() and 'True' in line:
            if 'split' in ''.join(lines[max(0,i-5):i+5]).lower():
                add('BUG', 'P3', i, line,
                    "shuffle=True en split — lookahead garantizado",
                    "Remover shuffle o poner shuffle=False. "
                    "Los datos temporales NUNCA deben shufflearse antes del split.")

        # Detectar train_test_split sin contexto de fecha
        if 'train_test_split' in line and 'date' not in ''.join(lines[max(0,i-10):i+10]):
            add('WARN', 'P3', i, line,
                "train_test_split sin referencia a fecha cercana",
                "Verificar que el split es temporal (por fecha), no aleatorio. "
                "Split correcto: train ≤ 2021-12-31 / val 2022 / test 2023")

    if date_splits:
        ok(f"Splits temporales detectados en {len(date_splits)} líneas:")
        for lineno, linetext in date_splits[:5]:  # mostrar máximo 5
            info(f"  L{lineno}: {linetext[:80]}")
    elif not split_found:
        add('BUG', 'P3', 0, '',
            "No se detectó split temporal por fecha en el trainer",
            "El split debe ser por fecha, no por índice ni aleatorio. "
            "Añadir: train = df[df['date'] <= '2021-12-31']")

    # ── PUNTO 4: Loss function ────────────────────────────────────────────
    head("PUNTO 4 — Loss function (critical_loss_optimized.py)")

    uses_import  = False
    uses_inline  = False

    for i, line in enumerate(lines, 1):
        # Importación correcta
        if 'critical_loss_optimized' in line and 'import' in line:
            ok(f"L{i}: Importa de critical_loss_optimized.py ✅")
            uses_import = True

        # Reimplementación inline (riesgo de divergencia)
        for pattern, desc in LOSS_REIMPL_PATTERNS:
            if re.search(pattern, line):
                add('WARN', 'P4', i, line,
                    f"Loss posiblemente reimplementada inline: {desc}",
                    "Si la reimplementación es idéntica al original → aceptable con comentario. "
                    "Si difiere en algún parámetro → usar siempre critical_loss_optimized.py "
                    "para evitar divergencia silenciosa.")
                uses_inline = True

    if not uses_import and not uses_inline:
        add('BUG', 'P4', 0, '',
            "No se detecta uso de critical_loss_optimized.py ni definición de loss asimétrica",
            "El trainer puede estar usando MSE puro (EF-05). "
            "MSE puro → LSTM no aprende dirección. "
            "Verificar qué loss usa y reemplazar con critical_loss_optimized.")

    # ── PUNTO 5: Checkpoint guardado ─────────────────────────────────────
    head("PUNTO 5 — Checkpoint con metadata completa")

    save_line = None
    for i, line in enumerate(lines, 1):
        if 'torch.save' in line or ('save' in line.lower() and 'checkpoint' in ''.join(lines[max(0,i-5):i+3]).lower()):
            save_line = (i, line)
            # Revisar qué guarda en las 10 líneas alrededor
            context = ''.join(lines[max(0,i-5):i+10])
            missing_keys = []
            for key in CHECKPOINT_REQUIRED_KEYS:
                if key not in context:
                    missing_keys.append(key)

            if missing_keys:
                add('WARN', 'P5', i, line,
                    f"Checkpoint puede no incluir: {missing_keys}",
                    f"Añadir al dict del checkpoint: {missing_keys}. "
                    f"Sin 'scaler': inferencia normaliza con parámetros distintos al train. "
                    f"Sin 'sha_parquet': no hay trazabilidad de con qué datos fue entrenado.")
            else:
                ok(f"L{i}: torch.save() con keys completas detectadas")

    if not save_line:
        add('BUG', 'P5', 0, '',
            "No se detecta torch.save() en el trainer",
            "El trainer no guarda checkpoints. Añadir torch.save() con metadata completa.")

    # ── RESUMEN FINAL ─────────────────────────────────────────────────────
    print("\n" + "═"*60)
    print("  RESUMEN AUDITORÍA TRAINER")
    print("═"*60)

    bugs   = [f for f in findings if f.severity == 'BUG']
    warns  = [f for f in findings if f.severity == 'WARN']
    infos  = [f for f in findings if f.severity == 'INFO']

    print(f"  ❌ BUGs:      {len(bugs)}")
    print(f"  ⚠️  Warnings: {len(warns)}")
    print(f"  ℹ️  Info:     {len(infos)}")

    if bugs:
        print("\n  ── BUGS CRÍTICOS (resolver antes de entrenar) ──")
        for f in bugs:
            print(f"\n  [{f.point}] Línea {f.line_no}: {f.detail}")
            if f.line_text:
                print(f"       Código: {f.line_text[:100]}")
            print(f"       Fix:    {f.fix}")

    if warns:
        print("\n  ── WARNINGS (revisar y decidir) ──")
        for f in warns:
            print(f"\n  [{f.point}] Línea {f.line_no}: {f.detail}")
            if f.line_text:
                print(f"       Código: {f.line_text[:100]}")
            print(f"       Fix:    {f.fix}")

    # Tabla de estado por punto
    print("\n  ── ESTADO POR PUNTO ──")
    for punto in ['P1', 'P2', 'P3', 'P4', 'P5']:
        punto_bugs  = [f for f in bugs  if f.point == punto]
        punto_warns = [f for f in warns if f.point == punto]
        if punto_bugs:
            estado = "❌ BUG"
        elif punto_warns:
            estado = "⚠️  WARN"
        else:
            estado = "✅ OK"
        labels = {
            'P1': 'Selección features tensor',
            'P2': 'Normalización anti-lookahead',
            'P3': 'Split temporal',
            'P4': 'Loss function',
            'P5': 'Checkpoint metadata',
        }
        print(f"  {punto} {labels[punto]}: {estado}")

    print()
    if bugs:
        print("  ⛔  TRAINER CON BUGS — corregir antes de entrenar.")
        print("       Regla R23: un fix a la vez, verificar antes del siguiente.\n")
        return False
    else:
        print("  ✅  TRAINER LISTO PARA ENTRENAR (revisar warnings)\n")
        return True


def main():
    parser = argparse.ArgumentParser(description='SPEL Trainer Auditor S24')
    parser.add_argument('--trainer', type=str, default=None,
                        help='Ruta al spel_trainer.py')
    args = parser.parse_args()

    print("\n" + "═"*60)
    print("  SPEL — Auditoría del Trainer (Paso 2 / S24)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("═"*60)

    trainer_path = find_trainer(args.trainer)
    if not trainer_path:
        print("\n  ❌ spel_trainer.py no encontrado.")
        print("  Rutas buscadas:")
        for p in TRAINER_SEARCH_PATHS:
            print(f"    {p}")
        print("\n  Soluciones:")
        print("  1. Pasar la ruta: python spel_trainer_audit.py --trainer /tu/ruta/spel_trainer.py")
        print("  2. Subir el archivo a una de las rutas buscadas")
        print("  3. Si el trainer está en Colab pero con otro nombre, renombrarlo a spel_trainer.py")
        sys.exit(1)

    passed = audit_trainer(trainer_path)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
