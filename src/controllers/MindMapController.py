"""Mind maps: topics that cite a page, grouped into branches off the notebook.

Built in two passes. The batches produce topics exactly as they produce cards,
so the map fills while it is read. Branches cannot be chosen batch by batch --
the first batch has no idea what the tenth will be about -- so `finalize` sees
every topic at once, asks for an outline, and stamps each item with its branch.
"""

from enums import ArtifactKind
from models.db_schema import MindMapNodeSet, MindMapOutline
from utils import get_logger

from .StructuredController import generate_structured
from .ArtifactController import ArtifactController

logger = get_logger(__name__)

#: Topics the outline call is shown. A 700-chunk book yields about 200; past
#: this the prompt gets long enough for a model to start losing numbers, and
#: anything beyond it lands on the "other" branch rather than being dropped.
MAX_OUTLINE_TOPICS = 300


class MindMapController(ArtifactController):
    schema = MindMapNodeSet
    prompt_key = "mindmap_prompt"
    field = "nodes"
    kind = ArtifactKind.MIND_MAP

    async def finalize(self, client, parser, items: list[dict]) -> list[dict] | None:
        if not items:
            return None

        considered = items[:MAX_OUTLINE_TOPICS]
        topics = "\n".join(
            parser.get("studio", "mindmap_topic_prompt", {"num": num, "topic": item["topic"]})
            for num, item in enumerate(considered, start=1)
        )
        prompt = parser.get("studio", "mindmap_outline_prompt", {"topics": topics})

        outline = await generate_structured(client, prompt, MindMapOutline)

        return group_into_branches(items, outline, other=parser.get("studio", "mindmap_other_branch"))


def group_into_branches(items: list[dict], outline: MindMapOutline, other: str) -> list[dict]:
    """Stamp each item with its branch, ordered branch by branch.

    Members are 1-based positions in *items*. A number out of range is ignored,
    and a topic named by two branches stays in the first -- a node drawn twice
    reads as two different ideas. Topics no branch claims go to *other* rather
    than disappearing: each one cites a real page.
    """
    branch_of: dict[int, str] = {}
    order: list[str] = []

    for branch in outline.branches:
        title = branch.title.strip()
        order.append(title)

        for member in branch.members:
            if 1 <= member <= len(items) and member not in branch_of:
                branch_of[member] = title

    unclaimed = len(items) - len(branch_of)

    if unclaimed:
        logger.info("Mind map: %d of %d topic(s) placed on the %r branch", unclaimed, len(items), other)
        order.append(other)

    rank = {title: at for at, title in enumerate(order)}
    stamped = [item | {"branch": branch_of.get(num, other)} for num, item in enumerate(items, start=1)]

    # Stable: within a branch, topics keep the order the document gave them.
    return sorted(stamped, key=lambda item: rank[item["branch"]])
