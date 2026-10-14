from .llm_client import LLMClient
from .llm_manager import LLMManager
from .plugin_api import Plugin, AppContext, SettingField, HookResult
from .plugin_loader import PluginLoader
from .chat_engine import ChatEngine
from .conversation_context import ConversationContext, Turn, build_context_block
from .preference_memory import PreferenceMemory, Preference
from .feedback_parser import FeedbackParser, Feedback
from .prompt_builder import PromptBuilder, PromptBundle
from .prompt_enhancer import PromptEnhancer
from .refine_loop import RefineLoop
from .generation_metrics import compute_metrics
from .prompt_ab import PromptAB
from .generation_history import GenerationHistory, GenerationRecord, get_history, reset_history
from .prompt_pipeline import PromptPipeline, get_pipeline

from .continuation_detector import is_continuation, is_reset_request, continuation_trigger, CONTINUATION_TRIGGERS
from .prompt_anchors import extract_anchors, apply_anchors, anchors_text, anchors_any, prompt_similarity, check_prompt_drift