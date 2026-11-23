"""多模态感知/表达适配层（P4）。

ASR/TTS/视觉不进工具注册表（不占 <=8 名额）：
- 语音输入：UI 录音 → asr.transcribe → 文本进会话；
- 语音输出：答案 → tts.synthesize → UI 播放；
- 图片/视频：UI 上传后自动 senses.vision.describe → 描述进上下文，
  另有 see_image 工具供对话中途追问。
"""