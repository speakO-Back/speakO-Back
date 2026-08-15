package com.example.speako.domain.presentation.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.List;

public class PresentationResponseDTO {

    @Getter @Builder
    public static class DetailDTO {
        private Long presentationId;
        private String topic;
        private int duration;
        private String tone;
        private String fileUrl;
        private List<SlideScriptDTO> slides;
    }

    @Getter @Builder
    public static class SlideScriptDTO {
        private Long slideId;
        private int slideOrder;
        private String slideTitle;
        private String rawText;
        private Long scriptId;
        private String content; // 슬라이드별 대본 내용
        private int version;
    }

    @Getter
    @Builder
    @AllArgsConstructor
    @NoArgsConstructor
    public static class FullScriptViewDTO {
        private Long presentationId;
        private String topic;
        private int duration;
        private String fileUrl;
        private String combinedScript; // 👈 전체 대본을 줄바꿈으로 이어 붙인 텍스트 (녹음 시 띄울 용도)
        private List<SlideScriptDTO> slideScripts; // 슬라이드별 개별 대본 리스트
    }
}