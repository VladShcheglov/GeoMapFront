import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse,FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List,Literal
import io
from PIL import Image
from dotenv import load_dotenv
import os


# --- Конфигурация Sentinel Hub ---
from sentinelhub import (
    SentinelHubRequest,
    DataCollection,
    MimeType,
    CRS,
    BBox,
    SHConfig,
)
load_dotenv()  

# --- Модель для входящих данных ---
class BboxRequest(BaseModel):
    bbox: List[float]
    layer_type: Literal["true_color", "ndvi"] # Ожидаем одно из двух значений





# --- Инициализация FastAPI ---
app = FastAPI()

# --- Настройка CORS ---
origins = ["http://localhost", "http://127.0.0.1", "http://127.0.0.1:5500", "null"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- КОНФИГУРАЦИЯ ---
# ❗️ ВАЖНО: Замените эти строки на ваши реальные Client ID и Client Secret
# В реальном проекте их лучше хранить в переменных окружения, а не в коде.
SH_CLIENT_ID = os.getenv("ClientID")
SH_CLIENT_SECRET = os.getenv("ClientSecret")

if not SH_CLIENT_ID or not SH_CLIENT_SECRET:
    raise ValueError("Пожалуйста, укажите SH_CLIENT_ID и SH_CLIENT_SECRET.")

config = SHConfig()
config.sh_client_id = SH_CLIENT_ID
config.sh_client_secret = SH_CLIENT_SECRET


# --- Evalscript ---
# Это небольшой JavaScript-код, который выполняется на серверах Sentinel Hub.
# Он определяет, какие каналы спутника и как смешивать, чтобы получить цветное изображение.
# Этот скрипт возвращает изображение в естественных цветах (True Color).
evalscript_true_color = """
    //VERSION=3
    function setup() {
        return {
            input: ["B04", "B03", "B02"], // Красный, Зелёный, Синий каналы
            output: { bands: 3 }
        };
    }

    function evaluatePixel(sample) {
        return [2.5 * sample.B04, 2.5 * sample.B03, 2.5 * sample.B02];
    }
"""
# Evalscript для NDVI
# Он вычисляет индекс и применяет цветовую карту
evalscript_ndvi = """
    //VERSION=3
    // Этот скрипт визуализирует NDVI (Normalized Difference Vegetation Index)
    
    function setup() {
        return {
            input: ["B04", "B08"], // Красный и ближний инфракрасный каналы
            output: { bands: 3 }   // Выходное изображение RGB
        };
    }

    // Функция для применения цветовой карты к значению NDVI
    const ramp = [
        [ -0.2, 0xc1a48e ], // почва/пустыня - коричневый
        [ 0.0, 0xf0e0b2 ],  // очень низкая растительность - бежевый
        [ 0.2, 0x336600 ],  // средняя растительность - тёмно-зелёный
        [ 0.6, 0x00ff00 ],  // высокая растительность - ярко-зелёный
        [ 1.0, 0x00ff00 ]   // очень высокая растительность - ярко-зелёный
    ];

    const visualizer = new ColorRampVisualizer(ramp);

    function evaluatePixel(sample) {
        // Формула NDVI
        let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);
        
        // Возвращаем цвет в зависимости от значения NDVI
        return visualizer.process(ndvi);
    }
"""
EVALSCRIPTS = {
    "true_color": evalscript_true_color,
    "ndvi": evalscript_ndvi
}

# --- Эндпоинт для получения снимка ---
# --- ЭНДПОИНТ ---
@app.post("/get-image")
async def get_image(request: BboxRequest):
    try:
        sentinel_bbox = BBox(bbox=request.bbox, crs=CRS.WGS84)
        
        selected_script = EVALSCRIPTS.get(request.layer_type)
        if not selected_script:
            raise HTTPException(status_code=400, detail="Неверный тип слоя")

        sentinel_request = SentinelHubRequest(
            evalscript=selected_script,
            input_data=[
                SentinelHubRequest.input_data(
                    data_collection=DataCollection.SENTINEL2_L1C,
                    time_interval=("2025-04-01", "2025-09-30"), # Свежие летние снимки
                )
            ],
            responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
            bbox=sentinel_bbox,
            size=[512, 512],
            config=config
        )
        
        image_data = sentinel_request.get_data()[0]
        image = Image.fromarray(image_data)
        #сохраняем на диск
        image.save('last_image.png')
        # image.show() # Можно временно раскомментировать для отладки
        
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        
        return StreamingResponse(buffer, media_type="image/png")

    except Exception as e:
        print(f"Произошла ошибка: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/download-image")
async def download_image():
    file_path = "last_image.png"
    return FileResponse(path=file_path,filename="sentinel_snapshot.png",media_type='image/png')