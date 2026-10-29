# 공 감지 모델 학습 데이터셋

| 구분 | 데이터셋·영상 | 제작자·출처 | 사용 용도 | 라이선스·공개 여부 |
| --- | --- | --- | --- | --- |
| 공개 데이터셋 | Futsal Scoring Model, 버전 1 | [mitchells Workspace / Roboflow Universe](https://universe.roboflow.com/mitchell-heaney-s-workspace/futsal-scoring-model) | 풋살공 검출 모델 학습 | CC BY 4.0 |
| 자체 촬영 데이터 | 팀 자체 촬영 영상 약 8분 | 프로젝트 팀 | 영상 자동 라벨링 후 풋살공 검출 모델 학습 | 외부 비공개 |

## 데이터 및 모델 구성

- 공개 데이터셋 페이지에 표시된 이미지 수는 737장임.
- YOLO11s를 공개 데이터셋과 자체 촬영 영상의 자동 라벨링 데이터를 활용해 추가 학습하고, 공 검출 모델 `ball_s.pt`를 생성함.
- 학습에 사용한 자체 영상과 성능 검증에 사용한 영상은 서로 겹치지 않음.
- 자체 촬영 영상과 학습 데이터는 외부에 공개하지 않음.

## 출처

mitchells Workspace, *Futsal Scoring Model*, Roboflow Universe, https://universe.roboflow.com/mitchell-heaney-s-workspace/futsal-scoring-model. 라이선스: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 공 검출 모델 학습에 활용함.
