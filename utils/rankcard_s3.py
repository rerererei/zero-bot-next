# utils/rankcard_s3.py
import io
import boto3
from PIL import Image
from config import RANKCARD_S3_BUCKET, RANKCARD_S3_PREFIX


# S3 クライアント生成（グローバルに持ってOK）
s3 = boto3.client("s3")


def load_rank_bg_from_s3(filename: str) -> Image.Image:
    """
    RankCard 背景画像を S3 から取得して Pillow Image として返す。
    filename は 'blue.png' のようにファイル名のみを渡す。

    例:
        load_rank_bg_from_s3("default.png")
        load_rank_bg_from_s3("pink.png")
    実際の S3 キーは:
        f"{RANKCARD_S3_PREFIX}{filename}"
        → "rankcard/default.png"
    """

    # S3 の実際のキーを組み立てる
    key = f"{RANKCARD_S3_PREFIX}{filename}"

    # S3 からオブジェクト取得
    resp = s3.get_object(
        Bucket=RANKCARD_S3_BUCKET,
        Key=key
    )

    # バイナリを読み取って Pillow Image に変換
    data = resp["Body"].read()
    img = Image.open(io.BytesIO(data))

    return img.convert("RGBA")
