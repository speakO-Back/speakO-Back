package com.example.speako.domain.user.controller;

import com.example.speako.domain.user.dto.UserRequestDTO;
import com.example.speako.domain.user.dto.UserResponseDTO;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.service.UserService;
import com.example.speako.global.common.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/auth")
public class UserController {

    private final UserService userService;

    @PostMapping("/signup")

    public ResponseEntity<UserResponseDTO.SignUpResultDTO> signUp(@RequestBody @Valid UserRequestDTO.SignUpDTO request) {
        User user = userService.signUp(request);

        UserResponseDTO.SignUpResultDTO result = UserResponseDTO.SignUpResultDTO.builder()
                .userId(user.getUserId())
                .email(user.getEmail())
                .name(user.getName())
                .createdAt(user.getCreatedAt())
                .build();

        return ResponseEntity.ok(result);
    }
    @PostMapping("/login")
    public ResponseEntity<ApiResponse<UserResponseDTO.LoginResultDTO>> login(
            @RequestBody @Valid UserRequestDTO.LoginDTO request
    ) {
        UserResponseDTO.LoginResultDTO result = userService.login(request);

        return ResponseEntity.ok(ApiResponse.onSuccess(result));
    }
}