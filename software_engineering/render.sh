#!/usr/bin/env bash
# Render every .puml under software_engineering/ to a .png beside it.
#
#   software_engineering/render.sh            # all diagrams
#   software_engineering/render.sh 3_sequence_diagrams/chat_agentic_rag.puml
#
# Needs java and graphviz (dot). The PlantUML jar is pinned and kept inside the
# project, in .tools/ (gitignored), and downloaded on first run, so nothing is
# installed outside the repository. Set PLANTUML_JAR to use another jar.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="1.2026.9"
# The digest GitHub publishes for plantuml-$VERSION.jar on the release page.
SHA256="aa4a1d06f8911c4b842beff4828dd955be431692b631a312d04c0e9fab9581de"
DEFAULT_JAR="$HERE/.tools/plantuml-$VERSION.jar"
JAR="${PLANTUML_JAR:-$DEFAULT_JAR}"

if [[ ! -f "$JAR" ]]; then
    if [[ "$JAR" != "$DEFAULT_JAR" ]]; then
        echo "PLANTUML_JAR=$JAR does not exist" >&2
        exit 1
    fi
    mkdir -p "$HERE/.tools"
    echo "Downloading PlantUML $VERSION into $HERE/.tools/ ..."
    curl -fsSL -o "$JAR.part" \
        "https://github.com/plantuml/plantuml/releases/download/v$VERSION/plantuml-$VERSION.jar"
    echo "$SHA256  $JAR.part" | sha256sum -c --quiet - || { echo "checksum mismatch" >&2; exit 1; }
    mv "$JAR.part" "$JAR"
fi

command -v dot >/dev/null || { echo "graphviz (dot) is required" >&2; exit 1; }

cd "$HERE"
if [[ $# -gt 0 ]]; then
    files=("$@")
else
    mapfile -t files < <(find . -path ./.tools -prune -o -name '*.puml' -print | sort)
fi

# -failfast2: stop at the first syntax error instead of drawing an error image.
java -Djava.awt.headless=true -jar "$JAR" -tpng -charset UTF-8 -failfast2 "${files[@]}"
echo "Rendered ${#files[@]} diagram(s)."
