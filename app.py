#!/usr/bin/env python3
"""Streamlit quiz app — mini exámenes aleatorios con retroalimentación."""

import json
import random
import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

BANCO_PATH = Path(__file__).parent / "banco_preguntas.json"
DB_PATH = Path(__file__).parent / "user_progress.db"


# ───── DB setup ─────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS respuestas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pregunta_id INTEGER,
            correcta INTEGER,
            timestamp TEXT,
            session_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS examenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            total INTEGER,
            correctas INTEGER,
            session_id TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_examen(total: int, correctas: int, session_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO examenes (timestamp, total, correctas, session_id) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), total, correctas, session_id),
    )
    conn.commit()
    conn.close()


def save_respuesta(pregunta_id: int, correcta: bool, session_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO respuestas (pregunta_id, correcta, timestamp, session_id) VALUES (?, ?, ?, ?)",
        (pregunta_id, int(correcta), datetime.now().isoformat(), session_id),
    )
    conn.commit()
    conn.close()


def get_stats(session_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute(
        "SELECT COUNT(*) FROM respuestas WHERE session_id=?", (session_id,)
    ).fetchone()[0]
    correctas = conn.execute(
        "SELECT COUNT(*) FROM respuestas WHERE correcta=1 AND session_id=?", (session_id,)
    ).fetchone()[0]
    examenes = conn.execute(
        "SELECT total, correctas FROM examenes WHERE session_id=? ORDER BY id", (session_id,)
    ).fetchall()
    conn.close()
    return {"total": total, "correctas": correctas, "examenes": examenes}


# ───── Load bank ─────

@st.cache_resource
def load_bank():
    with open(BANCO_PATH) as f:
        data = json.load(f)
    return data["preguntas"], data["meta"]


# ───── Session helpers ─────

def get_session_id() -> str:
    if "session_id" not in st.session_state:
        import uuid
        st.session_state.session_id = str(uuid.uuid4())[:8]
    return st.session_state.session_id


# ───── App ─────

def main():
    st.set_page_config(page_title="Mini Examen Médico", page_icon="🏥", layout="centered")

    # Custom CSS
    st.markdown("""
        <style>
        .correcta { color: #155724; background-color: #d4edda; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0; }
        .incorrecta { color: #721c24; background-color: #f8d7da; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0; }
        .justificacion { color: #004085; background-color: #cce5ff; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0; }
        .feedback-header { font-weight: bold; font-size: 1.1rem; margin-bottom: 0.5rem; }
        .stApp { max-width: 800px; margin: 0 auto; }
        </style>
    """, unsafe_allow_html=True)

    preguntas, meta = load_bank()

    init_db()
    session_id = get_session_id()

    # ─── Sidebar ───

    with st.sidebar:
        st.title("🏥 Mini Examen")
        st.caption(f"Banco: **{meta['total_preguntas']}** preguntas")
        st.caption(f"Archivos fuente: **{meta['total_archivos']}**")
        preg_con_caso = sum(1 for p in preguntas if p.get("caso_clinico") and p["caso_clinico"].strip() != p["pregunta"].strip())
        st.caption(f"Activas (con caso): **{preg_con_caso}**")

        num_q = st.slider("Preguntas por examen", 5, 50, 10, 5)

        if st.button("🔄 Nuevo examen", use_container_width=True, type="primary"):
            for k in ["preguntas_examen", "idx", "respuestas", "terminado"]:
                st.session_state.pop(k, None)
            for k in list(st.session_state.keys()):
                if k.startswith("q_answered_"):
                    del st.session_state[k]
            st.rerun()

        st.divider()
        stats = get_stats(session_id)
        if stats["total"] > 0:
            st.subheader("📊 Tu progreso")
            st.metric("Preguntas respondidas", stats["total"])
            pct = round(100 * stats["correctas"] / max(stats["total"], 1))
            st.metric("Aciertos", f"{pct}%")
            st.caption(f"{stats['correctas']}/{stats['total']} correctas")
            if stats["examenes"]:
                st.caption(f"Exámenes: {len(stats['examenes'])}")

    # ─── Main content ───

    if "preguntas_examen" not in st.session_state:
        # Solo preguntas que tienen caso clínico vinculado
        real_preguntas = [p for p in preguntas
                          if p.get("caso_clinico", "")
                          and p["caso_clinico"].strip() != p["pregunta"].strip()]
        if not real_preguntas:
            # Fallback: mostrar todo si el campo caso_clinico no está disponible
            st.info("Banco sin casos clínicos mapeados. Mostrando todas las preguntas.")
            real_preguntas = preguntas
        selected = random.sample(real_preguntas, min(num_q, len(real_preguntas)))
        st.session_state.preguntas_examen = selected
        st.session_state.idx = 0
        st.session_state.respuestas = []
        st.session_state.terminado = False

    if st.session_state.terminado:
        mostrar_resultado()
        return

    idx = st.session_state.idx
    preg = st.session_state.preguntas_examen
    total = len(preg)

    if idx >= total:
        st.session_state.terminado = True
        mostrar_resultado()
        return

    q = preg[idx]
    question_state = st.session_state.get(f"q_answered_{idx}")

    progreso = (idx) / total
    st.progress(progreso, text=f"Pregunta {idx + 1} de {total}")

    caso = q.get("caso_clinico", "")
    if caso and caso.strip() != q["pregunta"].strip():
        st.markdown(
            f'<div style="background:#e8f4f8;padding:1rem;border-radius:0.5rem;'
            f'border-left:4px solid #2196F3;margin:0.5rem 0">'
            f'<strong>📋 Caso clínico</strong><br>{caso}</div>',
            unsafe_allow_html=True,
        )

    st.markdown(f"### Pregunta {idx + 1}")
    st.markdown(q["pregunta"])

    opciones = q["opciones"]
    opts_labels = [f"{k}) {v}" for k, v in opciones.items()]
    opts_keys = list(opciones.keys())

    # ─── Radio + Responder — always visible ───

    selected_key = st.radio(
        "Selecciona tu respuesta:",
        opts_labels,
        key=f"radio_{idx}",
        index=None,
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        responder = st.button("✅ Responder", use_container_width=True, disabled=question_state or selected_key is None)

    # ─── When answered ───

    if responder and selected_key is not None and not question_state:
        letra = opts_keys[opts_labels.index(selected_key)]
        correcta_letra = q["respuesta_correcta"]
        es_correcta = letra in correcta_letra

        # Coincidencia case-insensitive para opciones
        opts_upper = {k.upper(): v for k, v in opciones.items()}
        resp_correcta_txt = ", ".join(
            f"{c}) {opts_upper.get(c.upper(), '?')}" for c in correcta_letra
        )

        st.session_state.respuestas.append({
            "pregunta_id": id(q),
            "correcta": es_correcta,
            "pagina": q.get("pagina", 0),
            "fuente": q.get("fuente", ""),
            "opcion_usuario": f"{letra}) {opciones.get(letra, opciones.get(letra.upper(), '?'))}",
            "respuesta_correcta": resp_correcta_txt,
            "justificacion": q.get("justificacion", ""),
        })

        save_respuesta(id(q), es_correcta, session_id)
        st.session_state[f"q_answered_{idx}"] = True
        question_state = True

    if question_state:
        resp = st.session_state.respuestas[-1]
        if resp["correcta"]:
            st.markdown(
                f'<div class="correcta"><div class="feedback-header">✅ ¡Correcto!</div>'
                f'{resp["respuesta_correcta"]}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="incorrecta"><div class="feedback-header">❌ Respuesta incorrecta</div>'
                f'La respuesta correcta era: **{resp["respuesta_correcta"]}** '
                f'según página **{resp["pagina"]}** del archivo **{resp["fuente"]}**</div>',
                unsafe_allow_html=True,
            )

        if resp.get("justificacion"):
            st.markdown(
                f'<div class="justificacion">📖 {resp["justificacion"]}</div>',
                unsafe_allow_html=True,
            )

        col_a, col_b, col_c = st.columns([1, 1, 1])
        with col_b:
            if idx + 1 < total:
                if st.button("⏭️ Siguiente pregunta", use_container_width=True, type="primary"):
                    st.session_state.idx += 1
                    st.rerun()
            else:
                if st.button("📊 Ver resultados", use_container_width=True, type="primary"):
                    for k in list(st.session_state.keys()):
                        if k.startswith("q_answered_"):
                            del st.session_state[k]
                    st.session_state.terminado = True
                    st.rerun()


def mostrar_resultado():
    respuestas = st.session_state.respuestas
    total = len(respuestas)
    correctas = sum(1 for r in respuestas if r["correcta"])
    pct = round(100 * correctas / max(total, 1))

    save_examen(total, correctas, get_session_id())

    st.balloons()
    st.markdown(f"## 📊 Resultado del examen")
    st.markdown(f"### {correctas}/{total} correctas — **{pct}%**")

    if pct >= 80:
        st.success("¡Excelente! Dominas el tema.")
    elif pct >= 60:
        st.info("Bien, pero hay áreas de mejora.")
    else:
        st.warning("Sigue practicando, repasa el material.")

    with st.expander("📋 Revisar respuestas"):
        for i, r in enumerate(respuestas):
            if r["correcta"]:
                st.markdown(f"**{i+1}.** ✅ {r['opcion_usuario']}")
            else:
                st.markdown(
                    f"**{i+1}.** ❌ Tu respuesta: {r['opcion_usuario']} → "
                    f"Correcta: **{r['respuesta_correcta']}** "
                    f"(p.{r['pagina']}, {r['fuente']})"
                )
            if r.get("justificacion"):
                with st.expander(f"📖 Ver justificación #{i+1}"):
                    st.markdown(r["justificacion"])

    st.divider()
    if st.button("🔄 Tomar otro examen", use_container_width=True, type="primary"):
        for k in ["preguntas_examen", "idx", "respuestas", "terminado"]:
            st.session_state.pop(k, None)
        for k in list(st.session_state.keys()):
            if k.startswith("q_answered_"):
                del st.session_state[k]
        st.rerun()


if __name__ == "__main__":
    main()
