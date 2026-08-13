package com.example.speako.domain.presentation.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class AiPresentationResponseDto {
    private Long presentationId; // 파이썬 서버가 돌려주는 프레젠테이션 ID (필요시 사용)
    private String message;      // 성공 메시지 등
    // 파이썬 AI 서버가 응답으로 주는 다른 필드들이 있다면 여기에 추가하시면 됩니다.
}
