package com.example.speako.domain.evaluation.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.Map;

public class EvaluationResponseDTO {

    @Getter
    @Builder
    @AllArgsConstructor
    @NoArgsConstructor
    public static class ResultDTO {
        private Long evaluationId;
        private Long userId;
        private Long slideId;
        private Long recordingId;
        private String audioFileName;
        private int audioDuration;
        private float totalScore;
        private float pronunciationScore;
        private int fillerWordCount;
        private Object fillerWordDetail; // JSON 데이터
        private float pauseScore;
        private Object pauseDetail; // JSON 데이터
        private String recognizedText;
        private String feedbackDetail;
        private LocalDateTime evaluatedAt;
    }
}