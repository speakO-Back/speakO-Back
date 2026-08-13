package com.example.speako.domain.presentation.dto;

import lombok.Builder;
import lombok.Getter;
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
}