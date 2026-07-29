package com.example.speako.domain.presentation.dto;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

public class PresentationRequestDTO {

    @Getter
    @Setter
    @NoArgsConstructor
    public static class CreateDTO{
        private String topic; //주제
        private int duration; //목표시간
        private String tone; //어조(formal, casual)
        private String guideline; //목차 및 추가 가이드라인
    }
}
