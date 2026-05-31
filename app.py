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
        if "casos_examen" in st.session_state:
            total_q = sum(len(g) for g in st.session_state.casos_examen)
            st.caption(f"Examen actual: **{len(st.session_state.casos_examen)}** casos, **{total_q}** preguntas")

        num_q = st.slider("Preguntas por examen", 5, 50, 10, 5)

        if st.button("🔄 Nuevo examen", use_container_width=True, type="primary"):
            for k in ["casos_examen", "caso_idx", "respuestas", "terminado"]:
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

    if "casos_examen" not in st.session_state:
        # Group questions by their caso_clinico to keep serial cases together
        real_preguntas = [p for p in preguntas
                          if p.get("caso_clinico", "")
                          and p["caso_clinico"].strip() != p["pregunta"].strip()]
        if not real_preguntas:
            st.info("Banco sin casos clínicos mapeados. Mostrando todas las preguntas.")
            real_preguntas = preguntas

        # Group: questions sharing the same caso_clinico text form a case group
        case_groups = {}
        standalone = []
        for q in real_preguntas:
            key = (q.get("caso_clinico", "") or "").strip()
            if key and key != q.get("pregunta", "").strip():
                case_groups.setdefault(key, []).append(q)
            else:
                standalone.append(q)

        all_groups = list(case_groups.values()) + [[q] for q in standalone]

        # Sample case groups (not individual questions) to reach ~num_q questions
        random.shuffle(all_groups)
        selected_groups = []
        total_qs = 0
        for g in all_groups:
            if total_qs + len(g) <= num_q * 1.5 or not selected_groups:
                selected_groups.append(g)
                total_qs += len(g)
            if total_qs >= num_q:
                break
        if not selected_groups:
            selected_groups = all_groups[:max(1, num_q // 3)]

        st.session_state.casos_examen = selected_groups
        st.session_state.caso_idx = 0
        st.session_state.respuestas = []
        st.session_state.terminado = False

    if st.session_state.terminado:
        mostrar_resultado()
        return

    casos = st.session_state.casos_examen
    caso_idx = st.session_state.caso_idx

    if caso_idx >= len(casos):
        st.session_state.terminado = True
        mostrar_resultado()
        return

    caso_actual = casos[caso_idx]
    caso_texto = caso_actual[0].get("caso_clinico", "") if len(caso_actual) > 0 else ""
    total_casos = len(casos)

    # ─── Caso clínico header (shown once per case) ───
    progreso_caso = caso_idx / total_casos
    total_q_in_exam = sum(len(g) for g in casos)
    preg_start_idx = sum(len(g) for g in casos[:caso_idx])
    st.progress(progreso_caso, text=f"Caso {caso_idx + 1} de {total_casos} ({total_q_in_exam} preguntas en total)")

    if caso_texto and caso_texto.strip() != caso_actual[0].get("pregunta", "").strip():
        st.markdown(
            f'<div style="background:#e8f4f8;padding:1rem;border-radius:0.5rem;'
            f'border-left:4px solid #2196F3;margin:0.5rem 0">'
            f'<strong>📋 Caso clínico</strong><br>{caso_texto}</div>',
            unsafe_allow_html=True,
        )

    # ─── Render all sub-questions of this case ───
    all_answered = True
    for sq_idx, q in enumerate(caso_actual):
        global_q_idx = preg_start_idx + sq_idx
        q_state_key = f"q_answered_{global_q_idx}"
        q_answered = st.session_state.get(q_state_key, False)

        st.markdown(f"### Pregunta {global_q_idx + 1}")
        st.markdown(q["pregunta"])

        opciones = q["opciones"]
        opts_labels = [f"{k}) {v}" for k, v in opciones.items()]
        opts_keys = list(opciones.keys())

        # Radio for this sub-question
        selected_key = st.radio(
            "Selecciona tu respuesta:",
            opts_labels,
            key=f"radio_global_{global_q_idx}",
            index=None,
            disabled=q_answered,
        )

        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            responder = st.button(
                "✅ Responder",
                key=f"btn_global_{global_q_idx}",
                use_container_width=True,
                disabled=q_answered or selected_key is None,
            )

        if responder and selected_key is not None and not q_answered:
            letra = opts_keys[opts_labels.index(selected_key)]
            correcta_letra = q["respuesta_correcta"]
            es_correcta = letra in correcta_letra
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
            st.session_state[q_state_key] = True
            st.rerun()

        if q_answered:
            resp = [r for r in st.session_state.respuestas if r["pregunta_id"] == id(q)]
            if resp:
                r = resp[-1]
                if r["correcta"]:
                    st.markdown(
                        f'<div class="correcta"><div class="feedback-header">✅ ¡Correcto!</div>'
                        f'{r["respuesta_correcta"]}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="incorrecta"><div class="feedback-header">❌ Respuesta incorrecta</div>'
                        f'La respuesta correcta era: **{r["respuesta_correcta"]}** '
                        f'según página **{r["pagina"]}** del archivo **{r["fuente"]}**</div>',
                        unsafe_allow_html=True,
                    )
                if r.get("justificacion"):
                    st.markdown(
                        f'<div class="justificacion">📖 {r["justificacion"]}</div>',
                        unsafe_allow_html=True,
                    )
        else:
            all_answered = False

        st.divider()

    # ─── Navigation between cases ───
    if all_answered:
        col_a, col_b, col_c = st.columns([1, 1, 1])
        with col_b:
            if caso_idx + 1 < total_casos:
                if st.button("⏭️ Siguiente caso", use_container_width=True, type="primary"):
                    st.session_state.caso_idx += 1
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
        for k in ["casos_examen", "caso_idx", "respuestas", "terminado"]:
            st.session_state.pop(k, None)
        for k in list(st.session_state.keys()):
            if k.startswith("q_answered_"):
                del st.session_state[k]
        st.rerun()


if __name__ == "__main__":
    main()
