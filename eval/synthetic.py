"""Generador del conjunto sintético E1: hechos que cambian con el tiempo, en español.

Cada cadena es un atributo de una entidad («ciudad de Ana», «base de datos de Atlas») con
1 a 4 versiones fechadas. Las preguntas están redactadas distinto que los hechos, y los
episodios de distracción mencionan entidades y valores sin afirmar el hecho («Ana visitó
Lisboa» no significa que viva allí). Todo es determinista con la semilla.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

START = datetime(2025, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 1, tzinfo=UTC)  # el «ahora» del experimento
MIN_GAP = timedelta(days=20)

PEOPLE = ["Ana", "Bruno", "Carla", "Diego", "Elena", "Fabio", "Gala", "Hugo", "Inés", "Jorge"]
PROJECTS = ["Atlas", "Boreal", "Cometa", "Delta", "Faro", "Géiser", "Halo", "Índigo"]


@dataclass(frozen=True)
class Attribute:
    key: str
    values: tuple[str, ...]
    statements: tuple[str, ...]  # primera versión: «{e} vive en {v}.»
    updates: tuple[str, ...]  # versiones siguientes: «{e} se mudó a {v}.»
    questions: tuple[str, ...]  # presente: «¿Dónde vive {e}?»
    past_questions: tuple[str, ...]  # con as_of: «¿Dónde vivía {e}?»


PERSON_ATTRIBUTES = (
    Attribute(
        "ciudad",
        ("Madrid", "Lisboa", "Sevilla", "Valencia", "Bilbao", "Oporto", "Granada", "Málaga"),
        ("{e} vive en {v}.", "{e} reside en {v}."),
        ("{e} se mudó a {v}.", "Ahora {e} vive en {v}."),
        ("¿Dónde vive {e}?", "¿En qué ciudad reside {e}?"),
        ("¿Dónde vivía {e}?",),
    ),
    Attribute(
        "empresa",
        ("Nébula", "Arbórea", "Cinética", "Dunas Labs", "Estela", "Fractal", "Granito"),
        ("{e} trabaja en {v}.", "{e} forma parte de la plantilla de {v}."),
        ("{e} cambió de trabajo y ahora está en {v}.", "{e} empezó a trabajar en {v}."),
        ("¿En qué empresa trabaja {e}?", "¿Para quién trabaja {e}?"),
        ("¿En qué empresa trabajaba {e}?",),
    ),
    Attribute(
        "deporte",
        ("natación", "escalada", "tenis", "ciclismo", "yoga", "pádel", "boxeo"),
        ("{e} practica {v}.", "El deporte de {e} es {v}."),
        ("{e} dejó su deporte anterior y ahora practica {v}.", "{e} se ha pasado a {v}."),
        ("¿Qué deporte practica {e}?", "¿A qué deporte se dedica {e}?"),
        ("¿Qué deporte practicaba {e}?",),
    ),
    Attribute(
        "comida",
        ("paella", "ramen", "tacos", "lasaña", "sushi", "cocido", "ceviche"),
        ("La comida favorita de {e} es {v}.", "Lo que más le gusta comer a {e} es {v}."),
        ("{e} cambió de plato favorito: ahora es {v}.", "Últimamente {e} prefiere {v}."),
        ("¿Cuál es la comida favorita de {e}?", "¿Qué es lo que más le gusta comer a {e}?"),
        ("¿Cuál era la comida favorita de {e}?",),
    ),
)

PROJECT_ATTRIBUTES = (
    Attribute(
        "base_de_datos",
        ("PostgreSQL", "MySQL", "SQLite", "MongoDB", "DynamoDB", "CockroachDB", "MariaDB"),
        ("El proyecto {e} usa {v} como base de datos.", "La base de datos de {e} es {v}."),
        ("{e} migró su base de datos a {v}.", "En {e} cambiamos la base de datos por {v}."),
        ("¿Qué base de datos usa {e}?", "¿Dónde guarda sus datos el proyecto {e}?"),
        ("¿Qué base de datos usaba {e}?",),
    ),
    Attribute(
        "despliegue",
        ("Heroku", "Fly.io", "AWS", "Render", "Vercel", "Railway", "Google Cloud"),
        ("{e} está desplegado en {v}.", "El proyecto {e} corre en {v}."),
        ("{e} se movió a {v}.", "Ahora {e} se despliega en {v}."),
        ("¿Dónde está desplegado {e}?", "¿En qué plataforma corre {e}?"),
        ("¿Dónde estaba desplegado {e}?",),
    ),
    Attribute(
        "responsable",
        ("Marta", "Luis", "Sara", "Pablo", "Nuria", "Óscar", "Rocío"),
        ("La persona responsable de {e} es {v}.", "{v} lidera el proyecto {e}."),
        ("{v} pasa a liderar {e}.", "Desde ahora {v} es responsable de {e}."),
        ("¿Quién es responsable de {e}?", "¿Quién lidera el proyecto {e}?"),
        ("¿Quién lideraba {e}?",),
    ),
    Attribute(
        "lenguaje",
        ("Python", "Go", "Rust", "Java", "TypeScript", "Kotlin", "Elixir"),
        ("El backend de {e} está escrito en {v}.", "{e} tiene el backend en {v}."),
        ("{e} reescribió el backend en {v}.", "El backend de {e} se migró a {v}."),
        ("¿En qué lenguaje está el backend de {e}?", "¿Con qué lenguaje se programó {e}?"),
        ("¿En qué lenguaje estaba el backend de {e}?",),
    ),
)

# Episodios que mencionan entidad y valor sin afirmar el hecho.
PERSON_DISTRACTORS = (
    ("ciudad", "{e} pasó un fin de semana en {v}."),
    ("comida", "{e} probó {v} en un restaurante nuevo."),
    ("deporte", "{e} vio un campeonato de {v} por la tele."),
    ("empresa", "{e} tuvo una entrevista en {v}, pero no la contrataron."),
)
PROJECT_DISTRACTORS = (
    ("base_de_datos", "En la reunión de {e} se habló de probar {v}."),
    ("despliegue", "{e} hizo una prueba de concepto en {v}."),
    ("lenguaje", "Alguien propuso reescribir {e} en {v}, pero no se aprobó."),
    ("responsable", "{v} revisó un pull request de {e}."),
)


@dataclass(frozen=True)
class Version:
    chain: str  # «Ana/ciudad»
    index: int  # 0 = primera versión
    text: str
    valid_from: datetime


@dataclass(frozen=True)
class Question:
    text: str
    chain: str
    expected: int  # índice de la versión correcta
    as_of: datetime | None  # None = pregunta sobre el presente


@dataclass(frozen=True)
class Distractor:
    text: str
    date: datetime


@dataclass(frozen=True)
class Dataset:
    versions: list[Version]  # en orden cronológico
    distractors: list[Distractor]
    questions: list[Question]
    now: datetime

    @property
    def chains(self) -> dict[str, list[Version]]:
        result: dict[str, list[Version]] = {}
        for version in self.versions:
            result.setdefault(version.chain, []).append(version)
        return result


def _dates(rng: random.Random, count: int) -> list[datetime]:
    """`count` fechas ordenadas entre START y NOW, separadas al menos MIN_GAP."""
    span_days = (NOW - START).days - 30
    while True:
        days = sorted(rng.sample(range(span_days), count))
        if all(b - a >= MIN_GAP.days for a, b in itertools.pairwise(days)):
            return [START + timedelta(days=d, hours=rng.randint(8, 19)) for d in days]


def _random_date(rng: random.Random, low: datetime, high: datetime) -> datetime:
    seconds = int((high - low).total_seconds())
    return low + timedelta(seconds=rng.randint(0, seconds))


def generate(seed: int = 2026) -> Dataset:
    rng = random.Random(seed)
    versions: list[Version] = []
    questions: list[Question] = []
    entities = [(name, PERSON_ATTRIBUTES) for name in PEOPLE] + [
        (name, PROJECT_ATTRIBUTES) for name in PROJECTS
    ]
    for entity, attributes in entities:
        for attribute in attributes:
            chain = f"{entity}/{attribute.key}"
            count = rng.choices([1, 2, 3, 4], weights=[20, 35, 30, 15])[0]
            values = rng.sample(attribute.values, count)
            dates = _dates(rng, count)
            chain_versions = []
            for index, (value, date) in enumerate(zip(values, dates, strict=True)):
                template = rng.choice(attribute.statements if index == 0 else attribute.updates)
                text = template.format(e=entity, v=value)
                chain_versions.append(Version(chain, index, text, date))
            versions += chain_versions
            question = rng.choice(attribute.questions).format(e=entity)
            questions.append(Question(question, chain, count - 1, None))
            if count >= 2:
                past = rng.randrange(count - 1)
                low = dates[past] + timedelta(days=1)
                high = dates[past + 1] - timedelta(days=1)
                text = rng.choice(attribute.past_questions).format(e=entity)
                questions.append(Question(text, chain, past, _random_date(rng, low, high)))

    distractors = []
    for entity, pool in [(name, PERSON_DISTRACTORS) for name in PEOPLE] + [
        (name, PROJECT_DISTRACTORS) for name in PROJECTS
    ]:
        for key, template in rng.sample(pool, 3):
            attributes = PERSON_ATTRIBUTES if pool is PERSON_DISTRACTORS else PROJECT_ATTRIBUTES
            attribute = next(a for a in attributes if a.key == key)
            value = rng.choice(attribute.values)
            date = _random_date(rng, START, NOW - timedelta(days=1))
            distractors.append(Distractor(template.format(e=entity, v=value), date))

    versions.sort(key=lambda v: v.valid_from)
    distractors.sort(key=lambda d: d.date)
    return Dataset(versions, distractors, questions, NOW)


if __name__ == "__main__":
    data = generate()
    updates = sum(1 for v in data.versions if v.index > 0)
    as_of = sum(1 for q in data.questions if q.as_of is not None)
    print(
        f"{len(data.chains)} cadenas, {len(data.versions)} versiones ({updates} cambios), "
        f"{len(data.distractors)} distractores, {len(data.questions)} preguntas "
        f"({len(data.questions) - as_of} presente, {as_of} con fecha)"
    )
    for version in data.versions[:4]:
        print(" ", version.valid_from.date(), version.text)
    for question in data.questions[:3]:
        print(" ", question.text, question.as_of.date() if question.as_of else "")
