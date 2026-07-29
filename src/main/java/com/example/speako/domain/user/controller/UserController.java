package com.example.speako.domain.user.controller;

import com.example.speako.domain.user.dto.UserRequestDTO;
import com.example.speako.domain.user.dto.UserResponseDTO;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.service.UserService;
import com.example.speako.global.common.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/auth")
public class UserController {

    private final UserService userService;

    @PostMapping("/signup")
    public ResponseEntity<ApiResponse<UserResponseDTO.SignUpResultDTO>> signUp(
            @RequestBody @Valid UserRequestDTO.SignUpDTO request
    ) {
        User user = userService.signUp(request);

        UserResponseDTO.SignUpResultDTO result = UserResponseDTO.SignUpResultDTO.builder()
                .userId(user.getUserId())
                .email(user.getEmail())
                .name(user.getName())
                .createdAt(user.getCreatedAt())
                .build();

        // ApiResponse.onSuccess 로 감싸서 리턴
        return ResponseEntity.ok(ApiResponse.onSuccess(result));
    }

    @PostMapping("/login")
    public ResponseEntity<ApiResponse<UserResponseDTO.LoginResultDTO>> login(
            @RequestBody @Valid UserRequestDTO.LoginDTO request
    ) {
        UserResponseDTO.LoginResultDTO result = userService.login(request);

        return ResponseEntity.ok(ApiResponse.onSuccess(result));
    }
    //이름변경
    @PatchMapping("/name")
    public ResponseEntity<ApiResponse<Void>> updateName(
            Authentication authentication,
            @RequestBody @Valid UserRequestDTO.UpdateNameDTO request
    ) {
        String email = authentication.getName(); // JWT 토큰에서 추출된 이메일
        userService.updateName(email, request);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }
    //이메일변경
    @PatchMapping("/email")
    public ResponseEntity<ApiResponse<Void>> updateEmail(
            Authentication authentication,
            @RequestBody @Valid UserRequestDTO.UpdateEmailDTO request
    ) {
        String email = authentication.getName();
        userService.updateEmail(email, request);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }
    //비밀번호 변경
    @PatchMapping("/password")
    public ResponseEntity<ApiResponse<Void>> updatePassword(
            Authentication authentication,
            @RequestBody @Valid UserRequestDTO.UpdatePasswordDTO request
    ) {
        String email = authentication.getName();
        userService.updatePassword(email, request);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }
    //회원탈퇴
    @DeleteMapping("/withdraw")
    public ResponseEntity<ApiResponse<Void>> withdraw(
            Authentication authentication,
            @RequestBody @Valid UserRequestDTO.WithdrawDTO request
    ) {
        String email = authentication.getName();
        userService.withdraw(email, request.getPassword());
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }
    //로그아웃
    @PostMapping("/logout")
    public ResponseEntity<ApiResponse<Void>> logout(Authentication authentication) {
        String email = authentication.getName();
        userService.logout(email);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }
}
