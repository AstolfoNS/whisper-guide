from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.database import get_database_session
from app.models.script import Script
from app.repositories.scripts import (
    add_scripts as persist_scripts,
)
from app.repositories.scripts import (
    get_script as find_script,
)
from app.repositories.scripts import (
    list_scripts as query_scripts,
)

router = APIRouter(prefix="/scripts", tags=["scripts"])
DatabaseSession = Annotated[Session, Depends(get_database_session)]
SUPPORTED_EXTENSIONS = {".txt", ".text", ".md"}


class ScriptSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    character_count: int
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def restore_utc_timezone(cls, value: datetime) -> datetime:
        # SQLite stores datetimes without timezone metadata; all stored values are UTC.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ScriptRead(ScriptSummary):
    content: str


def decode_text(data: bytes, name: str) -> str:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            content = data.decode("utf-16")
        else:
            try:
                content = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                content = data.decode("gb18030")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail=f"无法解码文件 {name}，请使用 UTF-8、带 BOM 的 UTF-16 或 GB18030 编码。",
        ) from error

    if not content.strip():
        raise HTTPException(
            status_code=422,
            detail=f"文件 {name} 为空或仅包含空白，不能作为讲稿。",
        )
    invalid_characters = (
        (ord(character) < 32 and character not in "\t\r\n") or ord(character) == 127
        for character in content
    )
    if any(invalid_characters):
        raise HTTPException(
            status_code=422,
            detail=f"文件 {name} 包含非文本内容，请上传纯文本讲稿。",
        )
    return content


@router.get("", response_model=list[ScriptSummary])
def list_scripts(session: DatabaseSession) -> list[Script]:
    return query_scripts(session)


@router.post("/upload", response_model=list[ScriptRead], status_code=status.HTTP_201_CREATED)
def upload_scripts(
    request: Request,
    session: DatabaseSession,
    files: Annotated[list[UploadFile], File()],
) -> list[Script]:
    settings = request.app.state.settings
    if len(files) > settings.script_max_upload_files:
        raise HTTPException(
            status_code=422,
            detail=f"每次最多上传 {settings.script_max_upload_files} 份讲稿。",
        )

    scripts: list[Script] = []
    for upload in files:
        name = (upload.filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
        if (
            not name
            or len(name) > 255
            or PurePosixPath(name).suffix.lower() not in SUPPORTED_EXTENSIONS
        ):
            raise HTTPException(
                status_code=422,
                detail="请上传文件名不超过 255 个字符的 .txt、.text 或 .md 纯文本讲稿。",
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise HTTPException(status_code=422, detail="讲稿文件名包含无效字符。")

        data = upload.file.read(settings.script_max_file_bytes + 1)
        if len(data) > settings.script_max_file_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件 {name} 超过单文件 {settings.script_max_file_bytes} 字节的限制。",
            )
        content = decode_text(data, name)
        scripts.append(Script(name=name, content=content, character_count=len(content)))

    # Validate the complete batch before writing, so invalid files save no partial batch.
    return persist_scripts(session, scripts)


@router.get("/{script_id}", response_model=ScriptRead)
def read_script(script_id: str, session: DatabaseSession) -> Script:
    script = find_script(session, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该讲稿。")
    return script
