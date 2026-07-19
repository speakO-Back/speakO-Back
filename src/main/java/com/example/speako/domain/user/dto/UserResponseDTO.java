package com.example.speako.domain.user.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

public class UserResponseDTO {
    @Getter
    @Builder
    @AllArgsConstructor
    public static class SignUpResultDTO {
        private Long userId;
        private String email;
        private String name;
        private LocalDateTime createdAt;
    }
    @Builder
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    public static class LoginResultDTO {
        private Long userId;
        private String email;
        private String accessToken;
    }
}
