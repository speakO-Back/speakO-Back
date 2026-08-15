package com.example.speako.domain.audio.dto;

import lombok.Getter;
import lombok.Setter;

public class TtsRequestDto {

    @Getter
    @Setter
    public static class FullTtsDto {
        private Long presentationId;
        private String voiceStyle; // 예: "hyeri_energetic", "daesung_calm"
        private float speed;       // 예: 1.0, 1.2, 1.5
    }

    @Getter
    @Setter
    public static class HighlightTtsDto {
        private Long highlightId;
        private String voiceStyle;
        private float speed;
    }
}