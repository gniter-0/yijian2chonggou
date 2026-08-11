"""gates：门禁判定层（review=人工准入判定，release=发布就绪判定）。

仅承载判定/准入逻辑；HTTP/页面层在 examcard.webui（webui 调 gates，反向禁止）。
"""
