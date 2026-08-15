package com.example.speako.domain.audio.dto;

import lombok.Builder;
import lombok.Getter;

@Getter
@Builder
public class TtsResponseDto {
    private String audioUrl; // 생성된 오디오 파일의 S3 URL 또는 스트리밍 주소
    private Integer duration; // 음성 재생 시간 (초)
}