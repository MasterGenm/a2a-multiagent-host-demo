# components/conversation_list.py (最终重构版)

import mesop as me
import pandas as pd

# 导入新的数据类和全局状态
from state.state import AppState, ChatMessage

# 我们不再依赖 host_agent_service 来创建对话，因为新模式下更简单。
# 但为了保留多代理框架的潜力，我们暂时保留它。
from state.host_agent_service import CreateConversation


@me.component
def conversation_list():
    """
    重构后的对话列表组件。
    它现在适配新的 AppState 结构，动态地构建对话视图。
    """
    state = me.state(AppState)
    
    # --- 适配器逻辑 ---
    # 动态地根据当前状态构建一个用于显示的对话列表。
    # 在这个简化模型中，我们只显示一个“当前对话”。
    # 如果 current_conversation_id 为空，表示是一个全新的会话。
    display_conversations = []
    if state.current_conversation_id or state.messages:
        conv_id = state.current_conversation_id if state.current_conversation_id else "Unsaved Conversation"
        conv_name = f"Chat {conv_id[:8]}..."
        # 消息数直接从 state.messages 的长度获取
        message_count = len(state.messages)
        
        display_conversations.append({
            'ID': conv_id,
            'Name': conv_name,
            'Status': 'Active',
            'Messages': message_count,
        })
    
    # 如果有多个对话保存在旧结构中，也可以在这里添加逻辑来显示它们。
    # for conv in state.legacy_conversations: ...

    df = pd.DataFrame(display_conversations)

    with me.box(
        style=me.Style(
            display='flex',
            justify_content='space-between',
            flex_direction='column',
        )
    ):
        me.table(
            df,
            on_click=on_click,
            header=me.TableHeader(sticky=True),
        )
        with me.content_button(
            type='raised',
            on_click=add_conversation,
            key='new_conversation',
            style=me.Style(
                display='flex',
                flex_direction='row',
                gap=5,
                align_items='center',
                margin=me.Margin(top=10),
            ),
        ):
            me.icon(icon='add')
            me.text("New Chat")


async def add_conversation(e: me.ClickEvent):
    """
    处理“创建新对话”按钮的点击事件。
    在新的简化模型中，这会清空当前状态，模拟开始一个新对话。
    """
    state = me.state(AppState)
    
    # 清空当前聊天状态
    state.messages = []
    state.user_input = ""
    state.tasks = {} # 清空关联的任务
    state.current_conversation_id = "" # 重置对话ID
    
    # 导航到主页，URL中不再带有conversation_id，表示是一个新会话
    me.navigate('/')
    yield


def on_click(e: me.TableClickEvent):
    """
    处理点击对话列表条目的事件。
    在当前简化模型中，由于只有一个活动对话，这个函数的作用不大。
    但在一个真正的多对话应用中，它会用于加载被点击的对话的状态。
    """
    # 可以在这里添加加载不同对话历史的逻辑（如果实现了多对话存储）
    print(f"Clicked on conversation at index {e.row_index}. Future implementation can load this chat.")
    yield