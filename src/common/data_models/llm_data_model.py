from dataclasses import dataclass
from typing import Optional, List, Tuple, TYPE_CHECKING, Any

from . import BaseDataModel
if TYPE_CHECKING:
    from src.llm_models.payload_content.tool_option import ToolCall

@dataclass
class LLMGenerationDataModel(BaseDataModel):
    content: Optional[str] = None
    reasoning: Optional[str] = None
    model: Optional[str] = None
    tool_calls: Optional[List["ToolCall"]] = None
    prompt: Optional[str] = None
    selected_expressions: Optional[List[int]] = None
    reply_set: Optional[List[Tuple[str, Any]]] = None