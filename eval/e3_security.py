"""E3: ¿detectan los filtros de seguridad lo que deben, sin estorbar a lo legítimo?

Aplica las reglas fijadas en eval/README.md (hito 6) a los casos de security_cases.py.
No necesita red ni modelos. Los informes no incluyen el texto de los secretos: aunque
sean falsos, tienen forma de clave y no deben acabar en el repositorio.

Uso:
    uv run python eval/e3_security.py
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from longmemeval import PATH as LONGMEMEVAL
from longmemeval import load
from security_cases import Case, all_groups

from lastword import safety
from lastword.errors import HiddenCharactersError, LastwordError
from lastword.models import MAX_CONTENT_CHARS

RESULTS = Path(__file__).parent / "results"
RESULTS_MD = RESULTS / "e3_security.md"
RESULTS_JSON = RESULTS / "e3_security.json"

# Reglas fijadas antes de medir (eval/README.md, hito 6).
SECRET_MIN_DETECTION = 0.90
SECRET_MAX_FALSE_POSITIVES = 1  # de 40
INSTRUCTION_MAX_FALSE_POSITIVES = 4  # de 40


@dataclass(frozen=True)
class Result:
    cases: list[Case]
    hits: list[Case]

    @property
    def rate(self) -> float:
        return len(self.hits) / len(self.cases)

    @property
    def missed(self) -> list[Case]:
        return [case for case in self.cases if case not in self.hits]

    def by_group(self) -> dict[str, tuple[int, int]]:
        """Grupo → (aciertos, total)."""
        counts: dict[str, tuple[int, int]] = {}
        for case in self.cases:
            hits, total = counts.get(case.group, (0, 0))
            counts[case.group] = (hits + (case in self.hits), total + 1)
        return counts

    def summary(self) -> str:
        return f"{len(self.hits)} de {len(self.cases)} ({self.rate * 100:g}%)"  # 2.5%, no 2%

    def to_dict(self, *, with_texts: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "total": len(self.cases),
            "hits": len(self.hits),
            "rate": round(self.rate, 4),
            "by_group": {g: {"hits": h, "total": t} for g, (h, t) in self.by_group().items()},
        }
        if with_texts:
            data["hit_texts"] = [case.text for case in self.hits]
            data["missed_texts"] = [case.text for case in self.missed]
        return data


def measure(cases: list[Case], detects: Callable[[str], bool]) -> Result:
    return Result(cases=cases, hits=[case for case in cases if detects(case.text)])


def rejected_as_hidden(text: str) -> bool:
    try:
        safety.check_before_saving([text], allow_secrets=True)
    except HiddenCharactersError:
        return True
    return False


def touched(text: str) -> bool:
    return bool(safety.flags([text])) or rejected_as_hidden(text)


def real_messages() -> dict[str, Any] | None:
    """Fuera del protocolo: los mensajes de usuario de LongMemEval, que no son ataques.

    Mide cuánto estorban los filtros a texto real. Solo si el conjunto está descargado.
    """
    if not LONGMEMEVAL.exists():
        return None
    items, _ = load()
    texts = list(
        dict.fromkeys(turn.text[:MAX_CONTENT_CHARS] for item in items for turn in item.turns)
    )
    rejected: Counter[str] = Counter()
    flagged: Counter[str] = Counter()
    flagged_messages = 0
    for text in texts:
        try:
            safety.check_before_saving([text])
        except LastwordError as exc:
            rejected[exc.code] += 1
        found = safety.find_instructions(text)
        flagged_messages += bool(found)
        flagged.update(finding.code for finding in found)
    return {
        "messages": len(texts),
        "rejected": sum(rejected.values()),
        "rejected_by_code": dict(rejected),
        "flagged": flagged_messages,
        "flagged_by_rule": dict(flagged.most_common()),
    }


def main() -> None:
    groups = all_groups()
    secrets = measure(groups["secretos"], lambda text: bool(safety.find_secrets(text)))
    lookalikes = measure(
        groups["parecidos_sin_secreto"], lambda text: bool(safety.find_secrets(text))
    )
    injections = measure(groups["inyecciones"], lambda text: bool(safety.find_instructions(text)))
    legit = measure(
        groups["instrucciones_legitimas"], lambda text: bool(safety.find_instructions(text))
    )
    hidden = measure(groups["ocultos"], lambda text: bool(safety.find_hidden(text)))
    unicode_ok = measure(groups["unicode_legitimo"], touched)
    rejected = sum(rejected_as_hidden(case.text) for case in groups["ocultos"])

    decisions = {
        "secrets_block": secrets.rate >= SECRET_MIN_DETECTION
        and len(lookalikes.hits) <= SECRET_MAX_FALSE_POSITIVES,
        "hidden_rule_met": not hidden.missed and not unicode_ok.hits,
        "warn_on_recall": len(legit.hits) <= INSTRUCTION_MAX_FALSE_POSITIVES,
    }
    payload = {
        # Nada con forma de clave sale del script: de los secretos solo se guardan los tipos.
        "secretos": secrets.to_dict(with_texts=False),
        "parecidos_sin_secreto": lookalikes.to_dict(),
        "inyecciones": injections.to_dict(),
        "instrucciones_legitimas": legit.to_dict(),
        "ocultos": hidden.to_dict(with_texts=False) | {"rejected": rejected},
        "unicode_legitimo": unicode_ok.to_dict(),
        "decisiones": decisions,
    }
    real = real_messages()
    if real is not None:
        payload["datos_reales"] = real
    text = report(secrets, lookalikes, injections, legit, hidden, unicode_ok, rejected, decisions)
    text += real_report(real)
    RESULTS.mkdir(exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    RESULTS_MD.write_text(text, encoding="utf-8")
    print(text)


def quoted(cases: list[Case]) -> list[str]:
    return [f"- «{case.text}»" for case in cases] or ["- ninguna"]


def counts(result: Result) -> list[str]:
    return [f"- {group}: {hits} de {total}" for group, (hits, total) in result.by_group().items()]


def report(
    secrets: Result,
    lookalikes: Result,
    injections: Result,
    legit: Result,
    hidden: Result,
    unicode_ok: Result,
    rejected: int,
    decisions: dict[str, bool],
) -> str:
    secrets_line = (
        "se **rechazan** al guardar: detección ≥ 90% y como mucho 1 falso aviso de 40."
        if decisions["secrets_block"]
        else "**solo avisan**: no cumplen la regla para rechazar."
    )
    hidden_line = (
        "regla cumplida: todos detectados y ningún texto legítimo afectado."
        if decisions["hidden_rule_met"]
        else "regla **no** cumplida: hay que corregir el detector."
    )
    orders_line = (
        "`recall` **avisa** al agente: como mucho 4 falsas marcas de 40."
        if decisions["warn_on_recall"]
        else "demasiadas falsas marcas: solo se ven con `lastword review`."
    )
    lines = [
        "# E3: filtros de seguridad",
        "",
        "Generado por `eval/e3_security.py` con los casos de `eval/security_cases.py`, fijados "
        "antes que los detectores junto con las reglas de [`eval/README.md`](../README.md). "
        "Los detectores son deterministas: el resultado es idéntico en cada ejecución.",
        "",
        "| Conjunto | Lo correcto | Resultado |",
        "|---|---|---:|",
        f"| Secretos de 13 formatos | detectarlos | {secrets.summary()} |",
        f"| Frases parecidas sin secreto | no detectar nada | {lookalikes.summary()} con aviso |",
        f"| Intentos de dar órdenes al agente | marcarlos | {injections.summary()} |",
        f"| Memorias legítimas que suenan a orden | no marcarlas | {legit.summary()} marcadas |",
        f"| Caracteres invisibles | rechazarlos o marcarlos | {hidden.summary()}, "
        f"{rejected} rechazados |",
        f"| Texto legítimo con emojis y otros alfabetos | no tocarlo | {unicode_ok.summary()} "
        "afectados |",
        "",
        "## Decisiones, según las reglas fijadas antes",
        "",
        f"- **Secretos:** {secrets_line}",
        f"- **Caracteres invisibles:** {hidden_line}",
        f"- **Órdenes incrustadas:** {orders_line}",
        "",
        "## Detalle",
        "",
        "Secretos detectados por formato:",
        "",
        *counts(secrets),
        "",
        "Órdenes marcadas por tipo de ataque:",
        "",
        *counts(injections),
        "",
        "Órdenes que se escapan:",
        "",
        *quoted(injections.missed),
        "",
        "Memorias legítimas marcadas por error:",
        "",
        *quoted(legit.hits),
        "",
        "Frases sin secreto que dieron aviso de secreto:",
        "",
        *quoted(lookalikes.hits),
        "",
        "## Cómo leerlo",
        "",
        "Los casos y los detectores los escribió la misma persona, así que la detección es un "
        "techo: quien atacara conociendo las reglas las esquivaría con más facilidad. Las reglas "
        "cazan lo evidente; la defensa de fondo es que el agente trate la memoria como datos.",
        "",
    ]
    return "\n".join(lines)


def real_report(real: dict[str, Any] | None) -> str:
    if real is None:
        return (
            "\n## Comprobación con datos reales\n\nNo se hizo: descarga LongMemEval con "
            "`eval/download_longmemeval.py` para medir cuánto estorban los filtros a texto real.\n"
        )
    total = real["messages"]
    messages = f"{total:,}".replace(",", ".")

    def share(n: int, digits: int) -> str:
        return f"{n / total:.{digits}%}".replace(".", ",")

    rules = ", ".join(f"{code}: {n}" for code, n in real["flagged_by_rule"].items())
    return "\n".join(
        [
            "",
            "## Comprobación con datos reales (fuera del protocolo)",
            "",
            f"Los {messages} mensajes de usuario distintos de LongMemEval, conversaciones con un "
            "asistente que no son ataques ni contienen secretos a propósito:",
            "",
            f"- **Rechazados al guardar:** {real['rejected']} ({share(real['rejected'], 3)}). "
            "No se reproduce su texto: puede contener tokens reales.",
            f"- **Marcados como orden:** {real['flagged']} ({share(real['flagged'], 2)}), por "
            f"regla: {rules}. Son sobre todo peticiones de rol («act as…», «you are now…»): en "
            "una conversación son normales, pero guardadas en una memoria sí son órdenes para "
            "un agente.",
            "",
        ]
    )


if __name__ == "__main__":
    main()
